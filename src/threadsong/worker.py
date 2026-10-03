import asyncio
import logging
import time

import httpx
from pydantic import ValidationError

from threadsong.config import Settings
from threadsong.db import Job, JobStore, LostLease
from threadsong.domain import (
    AmbiguousGeneration,
    Composer,
    PermanentError,
    PlatformAdapter,
    SongDraft,
    SongRequest,
    SongStorage,
    ThreadContext,
)

logger = logging.getLogger(__name__)


def failure_message(error_code: str | None) -> str:
    reasons = {
        "generation_blocked": (
            "Google's music filter blocked the song prompt I generated, so no audio was returned. "
            "Google doesn't identify the triggering text. Your song request is saved.\n\n"
            "Mention me again with a different style or mood to make a new attempt."
        ),
        "generation_http_429": (
            "Google's music service hit a rate or quota limit, so it didn't return a song. "
            "Your song request is saved. Please wait before trying again; "
            "if this continues, the bot's Google API quota needs checking."
        ),
        "generation_http_400": (
            "Google's music service rejected the generation request. Your song request is saved; "
            "the bot's request needs inspection before trying again."
        ),
        "generation_http_403": (
            "Google's music service denied access. Your song request is saved; "
            "the bot's API access or billing needs checking."
        ),
        "generation_http_404": (
            "The configured music model is unavailable. Your song request is saved; "
            "the bot's model configuration needs checking."
        ),
        "generation_timeout": (
            "Google's music service didn't respond in time. Your song request is saved. "
            "I haven't automatically started another generation because the first may still run."
        ),
    }
    return reasons.get(
        error_code,
        "I couldn't finish this song. The request is saved for inspection; "
        "I haven't automatically started another generation.",
    )


class Worker:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        platforms: dict[str, PlatformAdapter],
        composer: Composer,
        storage: SongStorage,
    ):
        self.settings, self.store, self.platforms = settings, store, platforms
        self.composer, self.storage = composer, storage
        self.wake = asyncio.Event()

    async def once(self) -> bool:
        job = await self.store.claim(self.settings.job_lease_seconds)
        if job is None:
            return False
        started = time.monotonic()
        stage = job.stage
        logger.info("Job %s stage %s started (attempt %s)", job.id, stage, job.attempts)
        try:
            await self.process(job)
            logger.info(
                "Job %s stage %s finished in %.2fs; status=%s next_stage=%s",
                job.id,
                stage,
                time.monotonic() - started,
                job.status,
                job.stage,
            )
        except LostLease:
            logger.warning("Job lease expired: %s", job.id)
        except Exception as exc:
            # Provider exception strings may contain secrets; log only our codes or class names.
            code = str(exc) if isinstance(exc, PermanentError) else type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                code = f"http_{exc.response.status_code}"
            logger.warning("Job %s stage %s failed: %s", job.id, job.stage, code)
            permanent = isinstance(exc, (PermanentError, ValidationError))
            if isinstance(exc, httpx.HTTPStatusError):
                permanent = (
                    400 <= exc.response.status_code < 500 and exc.response.status_code != 429
                )
            try:
                if job.stage == "failure" and (
                    permanent or job.attempts >= self.settings.max_job_attempts
                ):
                    payload = dict(job.payload)
                    payload.pop("context", None)
                    await self.store.save(job, status="failed", payload=payload)
                elif permanent or job.attempts >= self.settings.max_job_attempts:
                    # Failure delivery is a separate durable phase, with its own retry budget.
                    await self.store.save(job, stage="failure", error_code=code, attempts=0)
                else:
                    delay = min(60, 2**job.attempts)
                    await self.store.save(job, error_code=code, available_at=time.time() + delay)
            except LostLease:
                logger.warning("Could not checkpoint expired job: %s", job.id)
        return True

    async def progress(self, job: Job, request: SongRequest, payload: dict, text: str):
        """Bounded, best-effort UI updates; queue checkpoints still require a valid lease."""
        platform = self.platforms[request.platform]
        if not payload.get("progress_message_id"):
            try:
                async with asyncio.timeout(5):
                    message_id = await platform.reply(
                        request,
                        "🧵 Turning this thread into a song. I'll post the link here.",
                        "progress",
                    )
            except Exception as exc:
                logger.warning("Job %s progress create failed: %s", job.id, type(exc).__name__)
                return
            payload["progress_message_id"] = message_id
            # Persist before editing. A crash before this save reuses the same idempotency key.
            await self.store.save(job, release=False, payload=dict(payload))
        try:
            async with asyncio.timeout(5):
                await platform.update_message(payload["progress_message_id"], text)
        except Exception as exc:
            logger.warning("Job %s progress edit failed: %s", job.id, type(exc).__name__)

    async def process(self, job: Job):
        request = SongRequest.model_validate(job.request)
        platform = self.platforms[request.platform]
        payload = dict(job.payload)
        if job.stage == "context":
            context = await platform.context(request)
            if context is None:
                await self.store.save(job, status="ignored")
                return
            payload["context"] = context.model_dump()
            await self.progress(job, request, payload, "📖 Thread read. Preparing your song…")
            await self.store.save(job, payload=payload, stage="draft", attempts=0)
        elif job.stage == "draft":
            await self.progress(job, request, payload, "✍️ Summarizing your thread for the song…")
            draft = await self.composer.draft(ThreadContext.model_validate(payload["context"]))
            payload["draft"] = draft.model_dump()
            payload.pop("context", None)  # Do not retain raw thread text after drafting.
            await self.store.save(job, payload=payload, stage="generate", attempts=0)
        elif job.stage == "generate":
            draft = SongDraft.model_validate(payload["draft"])
            progress_text = f"🎵 Generating your song: {draft.title}"
            if draft.style:
                progress_text += f"\n\nStyle: {draft.style[:240]}"
            await self.progress(job, request, payload, progress_text)
            # Persist before the paid external call. Recovery never silently resubmits it.
            await self.store.save(job, release=False, stage="generating")
            audio = await self.composer.generate(SongDraft.model_validate(payload["draft"]))
            key = f"{job.id}/song.{audio.extension}"
            await self.progress(job, request, payload, "☁️ Music generated. Uploading your song…")
            try:
                await self.storage.upload(key, audio)
            except Exception as exc:
                raise AmbiguousGeneration("generated_audio_storage_failed") from exc
            payload.update(object_key=key, content_type=audio.content_type)
            title = payload["draft"]["title"].replace("\n", " ")
            # Persist the exact reply bytes before sending so idempotent retries cannot conflict.
            payload["reply_text"] = (
                f"Your thread has a soundtrack: {title}\n\n"
                f"Listen / share: {self.settings.public_base_url.rstrip('/')}/s/{job.share_token}"
            )
            await self.store.save(job, payload=payload, stage="publish", attempts=0)
        elif job.stage == "generating":
            # A restart occurred after marking generation in progress. Paid outcome is unknown.
            raise AmbiguousGeneration("generation_interrupted_check_provider")
        elif job.stage == "publish":
            payload["reply_id"] = await platform.reply(request, payload["reply_text"], "result")
            await self.progress(
                job, request, payload, "✅ Your song is ready! Listen in the reply below."
            )
            await self.store.save(job, payload=payload, status="complete", error_code=None)
        elif job.stage == "failure":
            # Only notify if we confirmed a mention; unrelated events fail silently in logs.
            if "context" in payload or "draft" in payload:
                await self.progress(job, request, payload, "❌ " + failure_message(job.error_code))
                await platform.reply(
                    request,
                    failure_message(job.error_code),
                    f"failure:{job.error_code or 'unknown'}",
                )
            payload.pop("context", None)
            await self.store.save(job, payload=payload, status="failed")
        else:
            raise PermanentError("unknown_job_stage")

    async def run(self):
        while True:
            self.wake.clear()
            try:
                if await self.once():
                    continue
            except Exception as exc:
                logger.error("Worker tick failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.settings.worker_poll_seconds)
            except TimeoutError:
                pass
