from threadsong.domain import SongRequest, ThreadContext


class DemoPlatform:
    def __init__(self):
        self.replies: list[dict] = []
        self.updates: list[dict] = []

    async def context(self, request: SongRequest) -> ThreadContext:
        return ThreadContext(
            instructions="@Songbot make this a country song",
            transcript="Alex: The deploy broke.\nSam: It was a missing comma.\nAlex: We shipped!",
        )

    async def reply(self, request: SongRequest, text: str, purpose: str) -> str:
        key = f"reply:{request.message_id}:{purpose}"
        if not any(reply["id"] == key for reply in self.replies):
            self.replies.append({"id": key, "thread_id": request.thread_id, "text": text})
        return key

    async def update_message(self, message_id: str, text: str) -> None:
        reply = next(reply for reply in self.replies if reply["id"] == message_id)
        reply["text"] = text
        self.updates.append({"id": message_id, "text": text})
