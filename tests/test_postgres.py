"""Opt-in check against a migrated, empty local Supabase database."""

import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url

from threadsong.db import Job, JobStore, LostLease


async def test_migrated_postgres_queue_and_privileges(song_request):
    url = os.environ.get("THREADSONG_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set THREADSONG_TEST_POSTGRES_URL for the local Postgres integration check")
    assert make_url(url).host in {"127.0.0.1", "localhost", "::1"}, "Local databases only"
    store = JobStore(url)
    workspace = "integration-" + str(uuid.uuid4())
    try:
        async with store.engine.connect() as conn:
            assert await conn.scalar(select(Job.id).limit(1)) is None, "Use an empty test queue"
            assert await conn.scalar(
                text("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.song_jobs'::regclass")
            )
            for role in ("anon", "authenticated"):
                assert not await conn.scalar(
                    text("SELECT has_table_privilege(:role, 'public.song_jobs', 'SELECT')"),
                    {"role": role},
                )
        req = song_request.model_copy(update={"workspace_id": workspace})
        job, created = await store.enqueue(req)
        duplicate, duplicate_created = await store.enqueue(
            req.model_copy(update={"event_id": "another-delivery"})
        )
        assert created and not duplicate_created and duplicate.id == job.id
        claims = await asyncio.gather(*(store.claim(60) for _ in range(8)))
        owned = [claim for claim in claims if claim is not None]
        assert len(owned) == 1 and owned[0].id == job.id
        old = owned[0]
        async with store.sessions() as session, session.begin():
            await session.execute(
                update(Job).where(Job.id == job.id).values(lease_until=time.time() - 1)
            )
        new = await store.claim(60)
        with pytest.raises(LostLease):
            await store.save(old, status="complete")
        await store.save(new, status="complete")
        assert (await store.shared(job.share_token)).id == job.id
        assert not await store.claim(60)
    finally:
        async with store.sessions() as session, session.begin():
            await session.execute(delete(Job).where(Job.workspace_id == workspace))
        await store.close()
