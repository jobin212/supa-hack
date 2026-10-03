"""Read-only Ando integration: uv run python scripts/smoke_ando.py CONVERSATION ROOT.

Reads a real thread, pages through replies, and assembles context for the first
human message mentioning the configured bot. Never posts or calls Google.
"""

import asyncio
import sys
from urllib.parse import quote

import httpx

from threadsong.config import Settings
from threadsong.domain import SongRequest
from threadsong.platforms.ando import AndoAdapter


async def main(conversation: str, root: str):
    settings = Settings(app_mode="demo")
    if settings.allowed_conversations and conversation not in settings.allowed_conversations:
        raise ValueError("Conversation is outside the configured allowlist")
    async with httpx.AsyncClient(timeout=30) as client:
        adapter = AndoAdapter(settings, client)
        messages = [await adapter.message(root, conversation)]
        print("PASS Read thread root")
        cursor = None
        seen = set()
        pages = 0
        while True:
            params = {"limit": 2}  # Small pages deliberately exercise pagination.
            if cursor:
                params["after"] = cursor
            data = await adapter._get(
                f"/conversation-messages/{quote(root, safe='')}/replies", params
            )
            assert data["conversation_id"] == conversation
            assert data["thread_root_id"] == root
            messages.extend(data["items"])
            pages += 1
            if not data["page_info"]["has_next_page"]:
                break
            cursor = data["page_info"]["next_cursor"]
            assert cursor and cursor not in seen, "Invalid pagination cursor"
            seen.add(cursor)
            if pages >= 100:
                raise RuntimeError("Smoke test exceeded 100 pages")
        print(f"PASS Read {len(messages) - 1} replies across {pages} pages")
        trigger = next(
            (
                m
                for m in messages
                if m.get("authorWorkspaceMembershipId") != settings.ando_agent_member_id
                and adapter.is_mentioned(m.get("content") or "")
            ),
            None,
        )
        if not trigger:
            print("NOT TESTED Bot mention context: no eligible mention in this thread")
            return
        request = SongRequest(
            platform="ando",
            event_id="read-only-smoke",
            workspace_id=settings.ando_workspace_id,
            conversation_id=conversation,
            message_id=trigger["id"],
            thread_id=root,
            author_id=trigger["authorWorkspaceMembershipId"],
        )
        context = await adapter.context(request)
        assert context and context.transcript
        print(f"PASS Bot mention context: {len(context.transcript)} characters")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: uv run python scripts/smoke_ando.py CONVERSATION_ID THREAD_ROOT_ID"
        )
    asyncio.run(main(*sys.argv[1:]))
