"""Real provider smoke test: uv run python scripts/smoke_music.py.

Makes one paid generation when no cached sample exists. Uses synthetic context,
creates a private songs bucket if missing, and leaves the sample for playback.
Supabase CLI credentials are read in memory only if .env has no Storage key.
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import httpx
from supabase.lib.client_options import AsyncClientOptions

from supabase import acreate_client
from threadsong.composer import GeminiComposer
from threadsong.config import Settings
from threadsong.domain import Audio, SongDraft
from threadsong.storage import SupabaseStorage


async def main():
    settings = Settings(app_mode="demo")  # Webhook setup is not needed for this test.
    url = settings.supabase_url or "https://vgmkqcatnmmfhkvzsiwb.supabase.co"
    key = settings.supabase_service_role_key.get_secret_value()
    if not key:
        cli = shutil.which("supabase") or str(Path.home() / ".local/bin/supabase")
        result = await asyncio.to_thread(
            subprocess.run,
            [
                cli,
                "projects",
                "api-keys",
                "--project-ref",
                "vgmkqcatnmmfhkvzsiwb",
                "--reveal",
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        keys = json.loads(result.stdout)
        key = next((k["api_key"] for k in keys if k.get("type") == "secret"), "")
        key = key or next(k["api_key"] for k in keys if k.get("name") == "service_role")
    directory = Path(".data/smoke")
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60) as storage_http, httpx.AsyncClient() as google:
        client = await acreate_client(
            url,
            key,
            options=AsyncClientOptions(
                httpx_client=storage_http,
                persist_session=False,
                auto_refresh_token=False,
            ),
        )
        buckets = await client.storage.list_buckets()
        bucket = settings.supabase_storage_bucket
        existing = next((b for b in buckets if b.id == bucket), None)
        if existing is None:
            await client.storage.create_bucket(bucket, options={"public": False})
        elif existing.public:
            raise RuntimeError("Expected a private songs bucket")
        print("PASS Storage authentication and private bucket", flush=True)
        cached = directory / "audio.json"
        if cached.exists():
            meta = json.loads(cached.read_text())
            audio = Audio(
                data=(directory / meta["file"]).read_bytes(),
                content_type=meta["mime"],
                extension=meta["extension"],
            )
            print("Reusing generated sample; no additional generation charge", flush=True)
        else:
            composer = GeminiComposer(
                settings.model_copy(update={"song_duration_seconds": 5}), google
            )
            audio = await composer.generate(
                SongDraft(
                    title="The Agents Have a Band",
                    style=(
                        "A very short five-second cheerful synth-pop jingle, "
                        "quick ending, no long intro"
                    ),
                    lyrics=(
                        "[Hook]\nShip the code, strike up the band!\nAgents singing, hand in hand!"
                    ),
                )
            )
            filename = "agents-jingle." + audio.extension
            (directory / filename).write_bytes(audio.data)
            cached.write_text(
                json.dumps(
                    {"file": filename, "mime": audio.content_type, "extension": audio.extension}
                )
            )
            print(
                f"PASS Lyria generation: {len(audio.data)} bytes, {audio.content_type}", flush=True
            )
        storage = SupabaseStorage(client, bucket)
        object_key = (
            "smoke/agents-jingle-"
            + hashlib.sha256(audio.data).hexdigest()[:12]
            + "."
            + audio.extension
        )
        await storage.upload(object_key, audio)
        print("PASS Audio upload", flush=True)
        # Exercise the application's five-minute link, then issue a longer demo link.
        short_link = await storage.signed_url(object_key)
        async with httpx.AsyncClient() as anonymous:
            download = await anonymous.get(short_link)
            download.raise_for_status()
            assert download.content == audio.data, "Downloaded bytes differ"
        print("PASS Signed link downloads identical audio without credentials", flush=True)
        link = await client.storage.from_(bucket).create_signed_url(object_key, 604800)
        (directory / "share-url.txt").write_text(link["signedURL"])
        print("Seven-day share URL saved to .data/smoke/share-url.txt", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Avoid dumping request URLs, headers, or credentials from exception strings.
        print(f"FAIL: {type(exc).__name__}", flush=True)
        cause = exc.__cause__
        if isinstance(cause, httpx.HTTPStatusError):
            print("Provider HTTP status:", cause.response.status_code)
            error = cause.response.json().get("error", {})
            print("Provider error:", error.get("status"), error.get("message", "")[:500])
        raise SystemExit(1) from None
