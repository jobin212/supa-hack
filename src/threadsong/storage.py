import asyncio
from pathlib import Path

from supabase import AsyncClient
from threadsong.domain import Audio


class SupabaseStorage:
    def __init__(self, client: AsyncClient, bucket: str):
        self.bucket = client.storage.from_(bucket)

    async def upload(self, key: str, audio: Audio):
        await self.bucket.upload(
            key,
            audio.data,
            file_options={
                "content-type": audio.content_type,
                "upsert": "true",
            },
        )

    async def signed_url(self, key: str) -> str:
        result = await self.bucket.create_signed_url(key, 300)
        return result["signedURL"]


class LocalStorage:
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, key: str) -> Path:
        path = (self.directory / key).resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise ValueError("Invalid object key")
        return path

    async def upload(self, key: str, audio: Audio):
        def write():
            path = self.path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(audio.data)
            temporary.replace(path)

        await asyncio.to_thread(write)

    async def signed_url(self, key: str) -> str:
        raise NotImplementedError("Local demo files are served by the token-protected share route")
