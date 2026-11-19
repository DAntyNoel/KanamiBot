from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class SentImageRecord:
    image_id: str
    folder: str
    message_id: int
    ts: int


class SendImageBuffer:
    """Short-lived mapping from bot-sent message ids to stored image ids."""

    def __init__(self, ttl_seconds: int = 30 * 60, max_items: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.history: list[SentImageRecord] = []

    def flush(self) -> None:
        current_time = int(time.time())
        self.history = [
            item for item in self.history if current_time - item.ts <= self.ttl_seconds
        ][-self.max_items :]

    def add(self, image_id: str, folder: str, msg_id: int | None) -> None:
        if msg_id is None:
            return
        self.flush()
        self.history.append(
            SentImageRecord(
                image_id=image_id,
                folder=folder,
                message_id=msg_id,
                ts=int(time.time()),
            )
        )
        if len(self.history) > self.max_items:
            self.history = self.history[-self.max_items :]

    def remove(self, msg_id: int) -> None:
        self.history = [item for item in self.history if item.message_id != msg_id]
        self.flush()

    def get(self, msg_id: int) -> tuple[str, str] | None:
        self.flush()
        for item in reversed(self.history):
            if item.message_id == msg_id:
                return item.image_id, item.folder
        return None


send_buffer = SendImageBuffer()
