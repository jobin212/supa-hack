"""Durable jobs with short, fenced leases; no network calls inside transactions."""

import secrets
import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, Float, Index, Integer, String, UniqueConstraint, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from threadsong.domain import SongRequest


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "song_jobs"
    __table_args__ = (
        UniqueConstraint("platform", "workspace_id", "message_id", name="song_jobs_source_key"),
        Index("song_jobs_due_idx", "status", "available_at", "lease_until"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    workspace_id: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str] = mapped_column(String(200))
    request: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    stage: Mapped[str] = mapped_column(String(32), default="context")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    share_token: Mapped[str] = mapped_column(String(64), unique=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class LostLease(Exception):
    pass


class JobStore:
    def __init__(self, url: str):
        # A session pooler or direct connection is required for asyncpg's prepared statements.
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.sqlite = self.engine.dialect.name == "sqlite"

    async def initialize_demo(self):
        if not self.sqlite:
            raise ValueError("Schema initialization is only automatic for local SQLite demo mode")
        database = self.engine.url.database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def enqueue(self, request: SongRequest) -> tuple[Job, bool]:
        job = Job(
            id=str(uuid.uuid4()),
            platform=request.platform,
            workspace_id=request.workspace_id,
            message_id=request.message_id,
            request=request.model_dump(),
            share_token=secrets.token_urlsafe(32),
        )
        async with self.sessions() as session:
            session.add(job)
            try:
                await session.commit()
                return job, True
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(Job).where(
                        Job.platform == request.platform,
                        Job.workspace_id == request.workspace_id,
                        Job.message_id == request.message_id,
                    )
                )
                if existing is None:
                    raise
                return existing, False

    async def claim(self, lease_seconds: int) -> Job | None:
        now = time.time()
        eligible = (
            Job.status == "pending",
            Job.available_at <= now,
            or_(Job.lease_until.is_(None), Job.lease_until < now),
        )
        async with self.sessions() as session, session.begin():
            query = (
                select(Job.id).where(*eligible).order_by(Job.available_at, Job.created_at).limit(1)
            )
            if not self.sqlite:
                query = query.with_for_update(skip_locked=True)
            job_id = await session.scalar(query)
            if job_id is None:
                return None
            # The second eligibility check also makes SQLite claims compare-and-set.
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, *eligible)
                .values(
                    lease_until=now + lease_seconds,
                    lease_token=str(uuid.uuid4()),
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
                .returning(Job)
            )
            return result.scalar_one_or_none()

    async def save(self, job: Job, *, release: bool = True, **values):
        values["updated_at"] = time.time()
        if release:
            values.update(lease_token=None, lease_until=None)
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.lease_token == job.lease_token,
                    Job.lease_until > time.time(),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise LostLease(job.id)
        for key, value in values.items():
            setattr(job, key, value)

    async def get(self, job_id: str) -> Job | None:
        async with self.sessions() as session:
            return await session.get(Job, job_id)

    async def shared(self, token: str) -> Job | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Job).where(
                    Job.share_token == token,
                    Job.status == "complete",
                )
            )

    async def close(self):
        await self.engine.dispose()
