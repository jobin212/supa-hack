from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class SongRequest(BaseModel):
    platform: str
    event_id: str
    workspace_id: str
    conversation_id: str
    message_id: str
    thread_id: str
    author_id: str


class ThreadContext(BaseModel):
    instructions: str
    transcript: str
    truncated: bool = False


class SongDraft(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=1000)
    style: str = Field(default="", max_length=1000)
    # Older jobs already checkpointed lyrics; keep them readable across a deployment.
    lyrics: str = Field(default="", max_length=12000)

    @model_validator(mode="after")
    def require_song_material(self):
        if not self.summary.strip() and not self.lyrics.strip():
            raise ValueError("A song needs a summary or a previously saved lyric draft")
        return self


class Audio(BaseModel):
    data: bytes
    content_type: str = "audio/mpeg"
    extension: str = "mp3"


class PlatformAdapter(Protocol):
    async def context(self, request: SongRequest) -> ThreadContext | None: ...

    async def reply(self, request: SongRequest, text: str, purpose: str) -> str: ...

    async def update_message(self, message_id: str, text: str) -> None: ...


class Composer(Protocol):
    async def draft(self, context: ThreadContext) -> SongDraft: ...

    async def generate(self, draft: SongDraft) -> Audio: ...


class SongStorage(Protocol):
    async def upload(self, key: str, audio: Audio) -> None: ...

    async def signed_url(self, key: str) -> str: ...


class PermanentError(Exception):
    """The job needs a configuration change or a new explicit request."""


class AmbiguousGeneration(PermanentError):
    """The provider may have accepted a paid request; do not resubmit automatically."""
