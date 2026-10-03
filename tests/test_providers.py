import base64
import json
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest
from supabase.lib.client_options import AsyncClientOptions

from supabase import acreate_client
from threadsong.composer import GeminiComposer, provider_error_details
from threadsong.domain import AmbiguousGeneration, Audio, PermanentError, SongDraft, ThreadContext
from threadsong.storage import SupabaseStorage


@pytest.mark.parametrize("style", ["", "hyperpop"])
async def test_gemini_interactions_summary_then_audio(settings, style):
    calls = []
    summary = "Joseph congratulated Bob and Sammie on an amazing sales quarter. Celebrate the team."

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["model"] == settings.gemini_text_model:
            assert body["store"] is False
            block = {
                "type": "text",
                "text": json.dumps(
                    {"title": "Sales Win", "style": style, "summary": summary}
                ),
            }
        else:
            assert body["model"] == settings.lyria_model
            assert summary in body["input"]
            assert "30 seconds" in body["input"]
            assert ("Style: hyperpop" in body["input"]) is bool(style)
            assert "Sales Win" not in body["input"]  # Display title doesn't steer Lyria.
            assert "SECRET-THREAD-MARKER" not in body["input"]
            assert "[Verse]" not in body["input"] and "[Chorus]" not in body["input"]
            block = {
                "type": "audio",
                "mime_type": "audio/mpeg",
                "data": base64.b64encode(b"mock-mp3").decode(),
            }
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [block]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        composer = GeminiComposer(settings, client)
        draft = await composer.draft(
            ThreadContext(instructions="Make a song", transcript="SECRET-THREAD-MARKER")
        )
        assert draft.summary == summary
        assert not draft.lyrics
        audio = await composer.generate(draft)
    assert audio == Audio(data=b"mock-mp3", content_type="audio/mpeg", extension="mp3")
    assert len(calls) == 2


async def test_legacy_lyric_checkpoint_still_generates(settings):
    draft = SongDraft.model_validate(
        {"title": "Existing song", "style": "country", "lyrics": "[Chorus] Saved words"}
    )

    def handler(request):
        prompt = json.loads(request.content)["input"]
        assert prompt.endswith("Sing these lyrics:\n[Chorus] Saved words")
        return httpx.Response(
            200,
            json={"output_audio": {"data": base64.b64encode(b"mock-mp3").decode()}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await GeminiComposer(settings, client).generate(draft)).data == b"mock-mp3"


async def test_supabase_sdk_upload_and_signed_url():
    def handler(request):
        assert request.headers["authorization"] == "Bearer server-test-key"
        if request.url.path == "/storage/v1/object/songs/job/song.mp3":
            assert request.method == "POST"
            mime = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
                + request.content
            )
            audio_part = next(
                part for part in mime.iter_parts() if part.get_content_type() == "audio/mpeg"
            )
            assert audio_part.get_payload(decode=True) == b"mock-mp3"
            assert request.headers["x-upsert"] == "true"
            return httpx.Response(200, json={"Key": "songs/job/song.mp3"})
        assert request.url.path == "/storage/v1/object/sign/songs/job/song.mp3"
        assert int(json.loads(request.content)["expiresIn"]) == 300
        return httpx.Response(200, json={"signedURL": "/object/sign/songs/job/song.mp3?token=test"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = await acreate_client(
            "https://test-project.supabase.co",
            "server-test-key",
            options=AsyncClientOptions(httpx_client=http, persist_session=False),
        )
        storage = SupabaseStorage(client, "songs")
        await storage.upload("job/song.mp3", Audio(data=b"mock-mp3"))
        assert await storage.signed_url("job/song.mp3") == (
            "https://test-project.supabase.co/storage/v1/object/sign/songs/job/song.mp3?token=test"
        )


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
async def test_generation_http_failure_preserves_status_without_retry(settings, status, caplog):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "PRIVATE-KEY-AND-PROMPT"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermanentError, match=f"generation_http_{status}") as exc:
            await GeminiComposer(settings, client).generate(
                SongDraft(title="Title", style="hyperpop", lyrics="Words")
            )
    assert isinstance(exc.value, AmbiguousGeneration) is (status >= 500)
    assert calls == 1
    assert "PRIVATE-KEY-AND-PROMPT" not in caplog.text
    assert f'"http_status": {status}' in caplog.text


def test_quota_diagnostics_do_not_include_raw_provider_messages():
    response = httpx.Response(
        429,
        json={
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "Private content",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaMetric": (
                                    "generativelanguage.googleapis.com/generate_requests"
                                ),
                                "quotaId": "RequestsPerDay",
                                "quotaValue": "10",
                                "description": "Private content",
                            }
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"},
                ],
            }
        },
    )
    details = provider_error_details(response)
    assert details["quotas"][0]["quotaId"] == "RequestsPerDay"
    assert details["retry_delay"] == "30s"
    assert "Private content" not in json.dumps(details)


async def test_lyria_content_rejection_uses_interactions_error_code(settings, caplog):
    def handler(request):
        return httpx.Response(
            400,
            json={"error": {"code": "prohibited_content", "message": "PRIVATE BLOCKED PROMPT"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermanentError, match="generation_blocked") as exc:
            await GeminiComposer(settings, client).generate(
                SongDraft(title="Title", style="hyperpop", lyrics="Words")
            )
    assert not isinstance(exc.value, AmbiguousGeneration)
    assert "prohibited_content" in caplog.text
    assert "PRIVATE BLOCKED PROMPT" not in caplog.text
