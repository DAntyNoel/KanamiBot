from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kanamibot.core.paths import DATA_DIR

from .config import CodexGPTConfig

PLUGIN_DATA_DIR = DATA_DIR / "codex_gpt"
SESSION_FILE = PLUGIN_DATA_DIR / "sessions.json"


@dataclass
class ChatSession:
    session_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict[str, str]] = field(default_factory=list)
    system_prompt: str | None = None
    model: str | None = None

    @classmethod
    def from_dict(cls, session_id: str, data: dict[str, Any]) -> ChatSession:
        messages = []
        for item in data.get("messages", []):
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content})

        return cls(
            session_id=session_id,
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            messages=messages,
            system_prompt=data.get("system_prompt")
            if isinstance(data.get("system_prompt"), str)
            else None,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "system_prompt": self.system_prompt,
            "model": self.model,
        }


class SessionStore:
    def __init__(self, config: CodexGPTConfig, path: Path = SESSION_FILE):
        self.config = config
        self.path = path
        self._lock = asyncio.Lock()
        self._sessions: dict[str, ChatSession] = {}
        self._loaded = False

    async def get(self, session_id: str) -> ChatSession:
        async with self._lock:
            await self._ensure_loaded()
            if session_id not in self._sessions:
                self._sessions[session_id] = ChatSession(session_id=session_id)
                await self._save_locked()
            return self._sessions[session_id]

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            await self._ensure_loaded()
            self._sessions[session_id] = ChatSession(session_id=session_id)
            await self._save_locked()

    async def set_system_prompt(self, session_id: str, prompt: str | None) -> None:
        async with self._lock:
            await self._ensure_loaded()
            session = self._sessions.setdefault(session_id, ChatSession(session_id=session_id))
            session.system_prompt = prompt
            session.updated_at = time.time()
            await self._save_locked()

    async def set_model(self, session_id: str, model: str | None) -> None:
        async with self._lock:
            await self._ensure_loaded()
            session = self._sessions.setdefault(session_id, ChatSession(session_id=session_id))
            session.model = model
            session.updated_at = time.time()
            await self._save_locked()

    async def forget_last_turn(self, session_id: str) -> int:
        async with self._lock:
            await self._ensure_loaded()
            session = self._sessions.setdefault(session_id, ChatSession(session_id=session_id))
            removed = 0
            while session.messages and removed < 2:
                session.messages.pop()
                removed += 1
            session.updated_at = time.time()
            await self._save_locked()
            return removed

    async def add_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        async with self._lock:
            await self._ensure_loaded()
            session = self._sessions.setdefault(session_id, ChatSession(session_id=session_id))
            session.messages.extend(
                [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ]
            )
            session.messages = _trim_history(
                session.messages,
                max_messages=self.config.max_history_messages,
                max_chars=self.config.max_history_chars,
            )
            session.updated_at = time.time()
            await self._save_locked()

    async def build_messages(
        self,
        session_id: str,
        user_text: str,
        stateless: bool = False,
    ) -> list[dict[str, str]]:
        async with self._lock:
            await self._ensure_loaded()
            session = self._sessions.setdefault(session_id, ChatSession(session_id=session_id))
            system_prompt = session.system_prompt or self.config.default_system_prompt
            history = [] if stateless else session.messages
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(
                _trim_history(
                    history,
                    max_messages=self.config.max_history_messages,
                    max_chars=self.config.max_history_chars,
                )
            )
            messages.append({"role": "user", "content": user_text})
            return messages

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._sessions = {}
            self._loaded = True
            await self._save_locked()
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}

        sessions = raw.get("sessions", {}) if isinstance(raw, dict) else {}
        self._sessions = {
            session_id: ChatSession.from_dict(session_id, data)
            for session_id, data in sessions.items()
            if isinstance(session_id, str) and isinstance(data, dict)
        }
        self._loaded = True

    async def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "sessions": {
                session_id: session.to_dict() for session_id, session in self._sessions.items()
            },
        }
        temp_path = self.path.with_suffix(f".tmp.{os.getpid()}")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)


def _trim_history(
    messages: list[dict[str, str]],
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    total_chars = 0
    for message in reversed(messages[-max_messages:]):
        content = message.get("content", "")
        total_chars += len(content)
        if total_chars > max_chars and kept:
            break
        kept.append({"role": message["role"], "content": content})
    kept.reverse()
    return kept
