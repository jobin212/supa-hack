import asyncio
import time

import httpx
import pytest
from sqlalchemy import update

from threadsong.app import create_app
from threadsong.composer import DemoComposer, GeminiComposer
from threadsong.db import Job, LostLease
from threadsong.demo import DemoPlatform
from threadsong.domain import AmbiguousGeneration, PermanentError, SongDraft
from threadsong.storage import LocalStorage
from threadsong.worker import Worker


def make_worker(settings, store, composer=None):
    platform = DemoPlatform()
    worker = Worker(
        settings,
        store,
        {"ando": platform},
        composer or DemoComposer(),
        LocalStorage(settings.local_data_dir / "songs"),
    )
    return worker, platform


async def test_duplicate_logical_requests_reuse_job_even_with_different_event_id(
    store, song_request
):
    first, created = await store.enqueue(song_request)
    second, second_created = await store.enqueue(
        song_request.model_copy(update={"event_id": "evt-2"})
    )
    assert created and not second_created
    assert first.id == second.id


async def test_concurrent_claims_do_not_share_a_job(store, song_request):
    await store.enqueue(song_request)
    claims = await asyncio.gather(store.claim(60), store.claim(60))
    assert sum(item is not None for item in claims) == 1


async def test_expired_worker_cannot_overwrite_new_lease(store, song_request):
    await store.enqueue(song_request)
    old = await store.claim(60)
    async with store.sessions() as session, session.begin():
        await session.execute(
            update(Job).where(Job.id == old.id).values(lease_until=time.time() - 1)
        )
    new = await store.claim(60)
    with pytest.raises(LostLease):
        await store.save(old, status="complete")
    await store.save(new, status="ignored")


async def test_full_workflow_and_restart_before_publish(settings, store, song_request):
    job, _ = await store.enqueue(song_request)
    worker, platform = make_worker(settings, store)
    for _ in range(3):
        assert await worker.once()
    checkpoint = await store.get(job.id)
    assert checkpoint.stage == "publish"
    assert "context" not in checkpoint.payload
    worker2 = Worker(settings, store, {"ando": platform}, DemoComposer(), worker.storage)
    assert await worker2.once()
    finished = await store.get(job.id)
    assert finished.status == "complete"
    assert finished.payload["object_key"].endswith(".wav")
    assert len(platform.replies) == 2
    assert finished.payload["progress_message_id"] == platform.replies[0]["id"]
    assert {edit["id"] for edit in platform.updates} == {platform.replies[0]["id"]}
    assert [edit["text"].split()[0] for edit in platform.updates] == ["📖", "✍️", "🎵", "☁️", "✅"]
    assert all(r["thread_id"] == "root-1" for r in platform.replies)
    assert finished.share_token in platform.replies[-1]["text"]
    assert not await worker2.once()


async def test_interrupted_generation_never_automatically_regenerates(
    settings, store, song_request
):
    class ShouldNotGenerate(DemoComposer):
        async def generate(self, draft):
            pytest.fail("Must not repeat an uncertain paid generation")

    job, _ = await store.enqueue(song_request)
    worker, platform = make_worker(settings, store, ShouldNotGenerate())
    await worker.once()
    await worker.once()
    claimed = await store.claim(60)
    await store.save(claimed, stage="generating")  # Simulates expired process after external call.
    await worker.once()
    await worker.once()
    result = await store.get(job.id)
    assert result.status == "failed"
    assert result.error_code == "generation_interrupted_check_provider"
    assert len(platform.replies) == 2  # Acknowledgment and a failure message.


async def test_generation_timeout_has_no_hidden_retry(settings):
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AmbiguousGeneration):
            await GeminiComposer(settings, client).generate(
                SongDraft(title="Title", style="country", lyrics="Words"),
            )
    assert calls == 1


@pytest.mark.parametrize(
    ("code", "message"),
    [("generation_http_429", "quota limit"), ("generation_blocked", "music filter blocked")],
)
async def test_rejected_music_request_reports_reason_without_regenerating(
    settings, store, song_request, code, message
):
    class QuotaComposer(DemoComposer):
        calls = 0

        async def generate(self, draft):
            self.calls += 1
            raise PermanentError(code)

    composer = QuotaComposer()
    worker, platform = make_worker(settings, store, composer)
    job, _ = await store.enqueue(song_request)
    for _ in range(4):
        await worker.once()
    result = await store.get(job.id)
    assert result.status == "failed"
    assert result.error_code == code
    assert message in platform.replies[-1]["text"]
    assert composer.calls == 1
    assert not await worker.once()


async def test_demo_api_share_and_health(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.get("/readyz")).status_code == 200
            assert (await c.post("/webhooks/ando", json={})).status_code == 503
            job_id = (await c.post("/demo")).json()["job_id"]
            for _ in range(4):
                await app.state.worker.once()
            job = (await c.get(f"/demo/jobs/{job_id}")).json()
            assert job["status"] == "complete"
            share = await c.get(job["share_url"])
            assert share.content.startswith(b"RIFF")
            assert share.headers["cache-control"] == "no-store"
            assert (await c.get("/s/" + "x" * 43)).status_code == 404


async def test_failure_delivery_eventually_stops_retrying(settings, store, song_request):
    class BrokenPlatform(DemoPlatform):
        async def reply(self, *args):
            raise RuntimeError("offline")

    settings.max_job_attempts = 1
    job, _ = await store.enqueue(song_request)
    worker, _ = make_worker(settings, store)
    worker.platforms["ando"] = BrokenPlatform()
    for _ in range(5):  # context, draft, generate, failed result, failed failure notification
        await worker.once()
    assert (await store.get(job.id)).status == "failed"
    assert not await worker.once()


@pytest.mark.parametrize("failure", ["create", "edit"])
async def test_progress_failure_does_not_interrupt_or_repeat_generation(
    settings, store, song_request, failure
):
    class CountingComposer(DemoComposer):
        calls = 0

        async def generate(self, draft):
            self.calls += 1
            return await super().generate(draft)

    class FlakyProgress(DemoPlatform):
        async def reply(self, request, text, purpose):
            if failure == "create" and purpose == "progress":
                raise httpx.ReadTimeout("unavailable")
            return await super().reply(request, text, purpose)

        async def update_message(self, message_id, text):
            raise httpx.ReadTimeout("unavailable")

    composer = CountingComposer()
    worker, _ = make_worker(settings, store, composer)
    platform = FlakyProgress()
    worker.platforms["ando"] = platform
    job, _ = await store.enqueue(song_request)
    for _ in range(4):
        await worker.once()
    assert (await store.get(job.id)).status == "complete"
    assert composer.calls == 1
    assert "Listen / share" in platform.replies[-1]["text"]


async def test_progress_creation_crash_reuses_message(settings, store, song_request):
    worker, platform = make_worker(settings, store)
    job, _ = await store.enqueue(song_request)
    # Simulate Ando accepting the initial message before its ID was checkpointed.
    existing = await platform.reply(
        song_request, "🧵 Turning this thread into a song. I'll post the link here.", "progress"
    )
    for _ in range(4):
        await worker.once()
    assert (await store.get(job.id)).payload["progress_message_id"] == existing
    assert len(platform.replies) == 2
