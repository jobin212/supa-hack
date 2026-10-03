import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from threadsong.app import create_app
from threadsong.db import Job
from threadsong.domain import PermanentError
from threadsong.platforms.ando import AndoAdapter, normalize_event, verify_signature


def signature(body, timestamp=1000):
    digest = hmac.new(b"test-secret", str(timestamp).encode() + b"." + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def test_signature_raw_body_and_replay_protection():
    body = b'{"text":"hello"}'
    header = signature(body)
    assert verify_signature(body, header, "test-secret", now=1001)
    assert not verify_signature(body + b" ", header, "test-secret", now=1001)
    assert not verify_signature(body, header, "test-secret", now=1301)
    assert not verify_signature(body, header, "test-secret", now=699)
    assert not verify_signature(body, header, "wrong-secret", now=1001)
    assert verify_signature(body, header + ",v1=" + "0" * 64, "test-secret", now=1001)


@pytest.mark.parametrize(
    "header",
    ["", "v1=abcd", "t=1.5,v1=a", "t=-1,v1=a", "t=0001000,v1=a", "t=" + "9" * 1000 + ",v1=a"],
)
def test_malformed_signature(header):
    assert not verify_signature(b"body", header, "secret", now=1000)


def event():
    return {
        "id": "evt-1",
        "type": "message.created",
        "workspace_id": "workspace-1",
        "data": {
            "object": {
                "id": "request-1",
                "conversation_id": "conversation-1",
                "authorWorkspaceMembershipId": "human-1",
            }
        },
        "related": {
            "message_id": "request-1",
            "conversation_id": "conversation-1",
            "thread_root_message_id": "root-1",
        },
    }


async def test_live_webhook_verification_durable_ack_and_duplicate_delivery(settings, store):
    # Exercise HTTP ingress with the real queue, while external providers stay disconnected.
    settings.app_mode = "live"
    settings.ando_workspace_id = "workspace-1"
    settings.ando_agent_member_id = "bot-1"
    settings.ando_webhook_signing_secret = SecretStr("test-secret")
    app = create_app(settings)
    app.state.store = store
    app.state.worker = SimpleNamespace(wake=asyncio.Event())
    raw = json.dumps(event()).encode()
    headers = {"Ando-Signature": signature(raw, int(time.time())), "Ando-Event-Id": "evt-1"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/webhooks/ando", content=raw)).status_code == 401
        assert (
            await client.post("/webhooks/ando", content=raw, headers=headers)
        ).status_code == 204
        assert (
            await client.post("/webhooks/ando", content=raw, headers=headers)
        ).status_code == 204
        headers["Ando-Event-Id"] = "mismatched-delivery"
        assert (
            await client.post("/webhooks/ando", content=raw, headers=headers)
        ).status_code == 400
        assert (await client.post("/webhooks/ando", content=b"x" * 256001)).status_code == 413
        assert (await client.post("/demo")).status_code == 404
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
    assert app.state.worker.wake.is_set()


async def test_setup_receiver_accepts_only_signed_tests_without_a_database(settings):
    settings.app_mode = "setup"
    settings.ando_webhook_signing_secret = SecretStr("test-secret")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert not hasattr(app.state, "store")
        assert not hasattr(app.state, "worker")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/healthz")).json()["mode"] == "setup"
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.post("/demo")).status_code == 404
            assert (await client.get("/s/" + "x" * 43)).status_code == 404
            for event_type, expected in [("webhook.test", 204), ("message.created", 503)]:
                raw = json.dumps({"id": "test-event", "type": event_type}).encode()
                assert (await client.post("/webhooks/ando", content=raw)).status_code == 401
                response = await client.post(
                    "/webhooks/ando",
                    content=raw,
                    headers={"Ando-Signature": signature(raw, int(time.time()))},
                )
                assert response.status_code == expected


def test_normalization_preserves_root_and_filters_self_workspace_and_channel(settings):
    settings.ando_workspace_id = "workspace-1"
    settings.ando_agent_member_id = "bot-1"
    normalized = normalize_event(event(), settings)
    assert normalized.thread_id == "root-1"
    data = event()
    data["data"]["object"]["authorWorkspaceMembershipId"] = "bot-1"
    assert normalize_event(data, settings) is None
    settings.ando_workspace_id = "different"
    assert normalize_event(event(), settings) is None
    settings.ando_workspace_id = "workspace-1"
    settings.ando_allowed_conversations = "different-channel"
    assert normalize_event(event(), settings) is None


async def test_thread_pagination_and_idempotent_thread_reply(settings, song_request):
    settings.ando_agent_member_id = "bot-1"
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "POST":
            assert req.headers["Idempotency-Key"] == "reply:request-1:result"
            assert json.loads(req.content) == {
                "markdown_content": "Listen: example",
                "thread_root_id": "root-1",
            }
            return httpx.Response(200, json={"data": {"id": "reply-1"}})
        if req.url.path.endswith("/replies"):
            second = req.url.params.get("after") == "page-2"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "thread_root_id": "root-1",
                        "conversation_id": "conversation-1",
                        "items": [
                            {
                                "id": "reply-2" if second else "reply-1",
                                "author_name": "Sam",
                                "authorWorkspaceMembershipId": "human-2",
                                "content": "Fixed it" if second else "The build broke",
                            }
                        ],
                        "page_info": {"has_next_page": not second, "next_cursor": "page-2"},
                    }
                },
            )
        root = req.url.path.endswith("root-1")
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "root-1" if root else "request-1",
                    "conversation_id": "conversation-1",
                    "content": "Deploy time" if root else "@Songbot make this country",
                    "author_name": "Alex",
                    "authorWorkspaceMembershipId": "human-1",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = AndoAdapter(settings, client)
        context = await adapter.context(song_request)
        assert "Fixed it" in context.transcript
        assert "Deploy time" in context.transcript
        assert not context.truncated
        assert await adapter.reply(song_request, "Listen: example", "result") == "reply-1"
    assert len(calls) == 5


async def test_unmentioned_message_does_not_fetch_thread(settings, song_request):
    def handler(req):
        assert req.url.path.endswith("request-1")
        return httpx.Response(
            200,
            json={
                "data": {
                    "content": "A regular conversation",
                    "conversation_id": "conversation-1",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await AndoAdapter(settings, client).context(song_request) is None


@pytest.mark.parametrize(
    ("content", "mentioned"),
    [
        ("<!workspace_membership:bot-1|Songbot> song time!", True),
        ("<!workspace_membership:bot-1|A renamed bot> sing this", True),
        ("<!workspace_membership:someone-else|Songbot> song time!", False),
        ("<!workspace_membership:bot-10|Songbot> song time!", False),
        ("@Songbot make this country", True),
        ("<@bot-1> song time!", True),
        ("@Songbot-else hello", False),
    ],
)
async def test_native_mentions_match_agent_identity(settings, content, mentioned):
    settings.ando_agent_member_id = "bot-1"
    async with httpx.AsyncClient() as client:
        assert AndoAdapter(settings, client).is_mentioned(content) is mentioned


async def test_ando_cannot_cross_conversation(settings, song_request):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "data": {
                    "content": "@Songbot go",
                    "conversation_id": "different-conversation",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermanentError, match="ando_conversation_mismatch"):
            await AndoAdapter(settings, client).context(song_request)


async def test_ando_progress_edits_replace_existing_message(settings):
    def handler(req):
        assert req.method == "PATCH"
        assert req.url.path == "/v1/conversation-messages/status-1"
        assert json.loads(req.content) == {"markdown_content": "🎵 Generating…"}
        return httpx.Response(200, json={"data": {"id": "status-1"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AndoAdapter(settings, client).update_message("status-1", "🎵 Generating…")
