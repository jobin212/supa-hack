import hashlib
import hmac
import re
import time
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from threadsong.config import Settings
from threadsong.domain import PermanentError, SongRequest, ThreadContext


def verify_signature(body: bytes, header: str, secret: str, now: int | None = None) -> bool:
    """Ando SDK v0.2.2 contract: HMAC-SHA256(secret, timestamp + '.' + raw body)."""
    if not secret or len(header) > 4096:
        return False
    timestamp = None
    signatures = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        value = value.strip()
        if not sep or not value:
            continue
        if key == "t":
            if not re.fullmatch(r"0|[1-9][0-9]{0,15}", value):
                return False
            timestamp = int(value)
        elif key == "v1" and re.fullmatch(r"[0-9a-f]{64}", value):
            signatures.append(value)
    if timestamp is None or abs((now if now is not None else int(time.time())) - timestamp) > 300:
        return False
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return any(hmac.compare_digest(digest.hexdigest(), candidate) for candidate in signatures)


class MessageRef(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    authorWorkspaceMembershipId: str = Field(min_length=1, max_length=200)


class EventData(BaseModel):
    object: MessageRef


class Related(BaseModel):
    conversation_id: str | None = None
    message_id: str | None = None
    thread_root_message_id: str | None = Field(default=None, max_length=200)


class MessageEvent(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    type: str
    workspace_id: str = Field(min_length=1, max_length=200)
    data: EventData
    related: Related


def normalize_event(data: dict, settings: Settings) -> SongRequest | None:
    if data.get("type") != "message.created":
        return None
    event = MessageEvent.model_validate(data)
    obj = event.data.object
    if event.workspace_id != settings.ando_workspace_id:
        return None
    if obj.authorWorkspaceMembershipId == settings.ando_agent_member_id:
        return None
    if settings.allowed_conversations and obj.conversation_id not in settings.allowed_conversations:
        return None
    if event.related.message_id not in (None, obj.id):
        raise ValueError("Inconsistent message reference")
    if event.related.conversation_id not in (None, obj.conversation_id):
        raise ValueError("Inconsistent conversation reference")
    return SongRequest(
        platform="ando",
        event_id=event.id,
        workspace_id=event.workspace_id,
        conversation_id=obj.conversation_id,
        message_id=obj.id,
        thread_id=event.related.thread_root_message_id or obj.id,
        author_id=obj.authorWorkspaceMembershipId,
    )


class AndoAdapter:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        self.base = "https://api.ando.so/v1"
        self.headers = {"x-api-key": settings.ando_api_key.get_secret_value()}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        result = await self.client.get(self.base + path, headers=self.headers, params=params)
        result.raise_for_status()
        return result.json().get("data", result.json())

    async def message(self, message_id: str, conversation_id: str) -> dict:
        message = await self._get(f"/conversation-messages/{quote(message_id, safe='')}")
        if message.get("conversation_id") != conversation_id:
            raise PermanentError("ando_conversation_mismatch")
        return message

    def is_mentioned(self, content: str) -> bool:
        member = re.escape(self.settings.ando_agent_member_id)
        if member and re.search(r"<!workspace_membership:" + member + r"\|[^>]*>", content):
            return True
        # Keep literal and Slack-style tokens for local fixtures and integrations;
        # Ando's native mention above is matched by membership ID, not display name.
        token = re.escape(self.settings.ando_mention_token)
        return bool(re.search(token + r"(?![\w-])", content, re.IGNORECASE)) or (
            f"<@{self.settings.ando_agent_member_id}>" in content
        )

    async def context(self, request: SongRequest) -> ThreadContext | None:
        trigger = await self.message(request.message_id, request.conversation_id)
        content = trigger.get("content") or ""
        if not self.is_mentioned(content):
            return None
        root = (
            trigger
            if request.thread_id == request.message_id
            else await self.message(
                request.thread_id,
                request.conversation_id,
            )
        )
        messages = [root]
        cursor = None
        seen_cursors: set[str] = set()
        truncated = False
        while True:
            params = {"limit": min(100, self.settings.max_thread_messages)}
            if cursor:
                params["after"] = cursor
            data = await self._get(
                f"/conversation-messages/{quote(request.thread_id, safe='')}/replies",
                params,
            )
            if data.get("thread_root_id") != request.thread_id:
                raise PermanentError("ando_thread_mismatch")
            if data.get("conversation_id") != request.conversation_id:
                raise PermanentError("ando_conversation_mismatch")
            messages.extend(data.get("items", []))
            page = data.get("page_info", {})
            has_more = page.get("has_next_page", False)
            if len(messages) >= self.settings.max_thread_messages:
                truncated = has_more or len(messages) > self.settings.max_thread_messages
                messages = messages[: self.settings.max_thread_messages]
                break
            if not has_more:
                break
            cursor = page.get("next_cursor")
            if not cursor or cursor in seen_cursors:
                raise PermanentError("ando_invalid_pagination")
            seen_cursors.add(cursor)
        lines = []
        seen_messages = set()
        for message in messages:
            if message.get("id") in seen_messages:
                continue
            seen_messages.add(message.get("id"))
            author = message.get("authorWorkspaceMembershipId", message.get("author_id"))
            if author == self.settings.ando_agent_member_id:
                continue
            lines.append(
                f"{message.get('author_name') or 'Participant'}: {message.get('content') or ''}"
            )
        transcript = "\n".join(lines)
        truncated = truncated or len(transcript) > self.settings.max_context_chars
        return ThreadContext(
            instructions=content[:2000],
            transcript=transcript[: self.settings.max_context_chars],
            truncated=truncated,
        )

    async def reply(self, request: SongRequest, text: str, purpose: str) -> str:
        result = await self.client.post(
            f"{self.base}/conversations/{quote(request.conversation_id, safe='')}/messages",
            headers={**self.headers, "Idempotency-Key": f"reply:{request.message_id}:{purpose}"},
            json={"markdown_content": text, "thread_root_id": request.thread_id},
        )
        result.raise_for_status()
        body = result.json()
        if body.get("success") is False:
            raise PermanentError("ando_reply_rejected")
        return body.get("data", body)["id"]

    async def update_message(self, message_id: str, text: str) -> None:
        result = await self.client.patch(
            f"{self.base}/conversation-messages/{quote(message_id, safe='')}",
            headers=self.headers,
            json={"markdown_content": text},
        )
        result.raise_for_status()
        if result.json().get("success") is False:
            raise PermanentError("ando_update_rejected")
