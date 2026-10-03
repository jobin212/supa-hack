import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import text
from supabase.lib.client_options import AsyncClientOptions

from supabase import acreate_client
from threadsong.composer import DemoComposer, GeminiComposer
from threadsong.config import Settings
from threadsong.db import JobStore
from threadsong.demo import DemoPlatform
from threadsong.domain import SongRequest
from threadsong.platforms.ando import normalize_event, verify_signature
from threadsong.storage import LocalStorage, SupabaseStorage
from threadsong.worker import Worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # httpx INFO logs include URLs; signed Storage tokens must not reach logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.app_mode == "setup":
            # Bootstrap the public receiver before database/model credentials are available.
            # Only authenticated webhook.test events are acknowledged in this mode.
            logging.getLogger(__name__).info(
                "Setup configuration: Postgres=%s Storage=%s",
                settings.database_url.get_secret_value().startswith("postgresql+asyncpg://"),
                bool(
                    settings.supabase_url and settings.supabase_service_role_key.get_secret_value()
                ),
            )
            yield
            return
        store = JobStore(settings.database_url.get_secret_value())
        if settings.app_mode == "demo":
            await store.initialize_demo()
        async with (
            httpx.AsyncClient(timeout=20, follow_redirects=False) as client,
            httpx.AsyncClient(timeout=20, follow_redirects=False) as storage_http,
        ):
            supabase = None
            if settings.app_mode == "demo":
                platform = DemoPlatform()
                composer = DemoComposer()
                storage = LocalStorage(settings.local_data_dir / "songs")
                platforms = {"demo": platform}
            else:
                from threadsong.platforms.ando import AndoAdapter

                supabase = await acreate_client(
                    settings.supabase_url,
                    settings.supabase_service_role_key.get_secret_value(),
                    options=AsyncClientOptions(
                        httpx_client=storage_http,
                        persist_session=False,
                        auto_refresh_token=False,
                    ),
                )
                composer = GeminiComposer(settings, client)
                storage = SupabaseStorage(supabase, settings.supabase_storage_bucket)
                platforms = {"ando": AndoAdapter(settings, client)}
            worker = Worker(settings, store, platforms, composer, storage)
            app.state.store, app.state.worker, app.state.storage = store, worker, storage
            app.state.platforms = platforms
            task = asyncio.create_task(worker.run()) if settings.run_worker else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                await store.close()

    app = FastAPI(
        title="Threadsong", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.get("/health")
    @app.get("/healthz")
    async def health():
        return {"ok": True, "service": "threadsong", "mode": settings.app_mode}

    @app.get("/readyz")
    async def ready(request: Request):
        if settings.app_mode == "setup":
            raise HTTPException(503, "Webhook setup only; live processing is not configured")
        try:
            async with request.app.state.store.engine.connect() as connection:
                await connection.execute(text("SELECT id FROM song_jobs LIMIT 0"))
        except Exception:
            raise HTTPException(503, "Database unavailable or schema not initialized") from None
        return {"ready": True}

    @app.post("/webhooks/ando", status_code=204)
    async def ando_webhook(request: Request):
        if settings.app_mode == "demo":
            raise HTTPException(503, "Configure live mode for Ando webhooks")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 256_000:
                raise HTTPException(413, "Webhook too large")
        if not verify_signature(
            bytes(raw),
            request.headers.get("Ando-Signature", ""),
            settings.ando_webhook_signing_secret.get_secret_value(),
        ):
            raise HTTPException(401, "Invalid webhook signature")
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("Invalid event")
            header_id = request.headers.get("Ando-Event-Id")
            if header_id and event.get("id") != header_id:
                raise ValueError("Event ID mismatch")
        except (ValueError, ValidationError):
            raise HTTPException(400, "Invalid event") from None
        if settings.app_mode == "setup":
            if event.get("type") == "webhook.test":
                return Response(status_code=204)
            raise HTTPException(503, "Song processing is not configured; retry delivery later")
        try:
            normalized = normalize_event(event, settings)
        except (ValueError, ValidationError):
            raise HTTPException(400, "Invalid event") from None
        if normalized:
            try:
                async with asyncio.timeout(5):
                    job, created = await request.app.state.store.enqueue(normalized)
            except TimeoutError:
                raise HTTPException(503, "Queue unavailable; retry delivery") from None
            request.app.state.worker.wake.set()
            logging.getLogger(__name__).info(
                "Ando event %s accepted; job=%s new=%s", normalized.event_id, job.id, created
            )
        return Response(status_code=204)

    @app.get("/s/{token}")
    async def share(token: str, request: Request):
        if settings.app_mode == "setup":
            raise HTTPException(404, "Song not found")
        if len(token) != 43:
            raise HTTPException(404, "Song not found")
        job = await request.app.state.store.shared(token)
        if job is None:
            raise HTTPException(404, "Song not found")
        storage = request.app.state.storage
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if isinstance(storage, LocalStorage):
            return FileResponse(
                storage.path(job.payload["object_key"]),
                media_type=job.payload["content_type"],
                headers=headers,
            )
        return RedirectResponse(
            await storage.signed_url(job.payload["object_key"]), status_code=307, headers=headers
        )

    if settings.app_mode == "demo":

        @app.post("/demo", status_code=202)
        async def demo(request: Request):
            identifier = str(uuid.uuid4())
            job, _ = await request.app.state.store.enqueue(
                SongRequest(
                    platform="demo",
                    event_id=identifier,
                    workspace_id="demo",
                    conversation_id="demo-channel",
                    message_id=identifier,
                    thread_id="demo-thread",
                    author_id="demo-person",
                )
            )
            request.app.state.worker.wake.set()
            return {"job_id": job.id, "note": "Demo uses a test tone, not generated music."}

        @app.get("/demo/jobs/{job_id}")
        async def demo_job(job_id: str, request: Request):
            job = await request.app.state.store.get(job_id)
            if job is None:
                raise HTTPException(404, "Job not found")
            result = {
                "id": job.id,
                "status": job.status,
                "stage": job.stage,
                "error_code": job.error_code,
            }
            if job.status == "complete":
                result["share_url"] = settings.public_base_url.rstrip("/") + "/s/" + job.share_token
            return result

    return app
