from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from nonebot.log import logger

from .paths import DATA_DIR

CHAT_HISTORY_DIR = DATA_DIR / "daily_report"
CHAT_HISTORY_DB = CHAT_HISTORY_DIR / "chat_logs.db"

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def ensure_chat_history_db() -> Path:
    global _INITIALIZED

    if _INITIALIZED:
        return CHAT_HISTORY_DB

    with _INIT_LOCK:
        if _INITIALIZED:
            return CHAT_HISTORY_DB

        CHAT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(CHAT_HISTORY_DB) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_logs (
                    timestamp TEXT,
                    group_id TEXT,
                    user_id TEXT,
                    content TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_logs_group_time
                ON chat_logs(group_id, timestamp)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_logs_time
                ON chat_logs(timestamp)
                """
            )
            conn.commit()

        _INITIALIZED = True
        return CHAT_HISTORY_DB


def record_group_message(
    group_id: int | str,
    user_id: int | str,
    content: str,
    *,
    timestamp: datetime | None = None,
) -> None:
    text = content.strip()
    if not text:
        return

    ensure_chat_history_db()
    timestamp_text = (timestamp or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(CHAT_HISTORY_DB, timeout=10) as conn:
        conn.execute(
            "INSERT INTO chat_logs VALUES (?, ?, ?, ?)",
            (timestamp_text, str(group_id), str(user_id), text),
        )
        conn.commit()


def get_today_logs(
    group_id: int | str,
    *,
    target_date: date | None = None,
) -> list[tuple[str, str]]:
    day = (target_date or datetime.now().date()).strftime("%Y-%m-%d")
    return get_logs_for_day(group_id, day)


def get_logs_for_day(group_id: int | str, day: str) -> list[tuple[str, str]]:
    ensure_chat_history_db()
    with sqlite3.connect(CHAT_HISTORY_DB, timeout=10) as conn:
        rows = conn.execute(
            """
            SELECT user_id, content
            FROM chat_logs
            WHERE group_id = ? AND timestamp LIKE ?
            ORDER BY timestamp ASC
            """,
            (str(group_id), f"{day}%"),
        ).fetchall()
    return [(str(user_id), str(content or "")) for user_id, content in rows]


def get_today_group_ids(*, target_date: date | None = None) -> list[int]:
    day = (target_date or datetime.now().date()).strftime("%Y-%m-%d")
    ensure_chat_history_db()
    with sqlite3.connect(CHAT_HISTORY_DB, timeout=10) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT group_id
            FROM chat_logs
            WHERE timestamp LIKE ?
            ORDER BY group_id ASC
            """,
            (f"{day}%",),
        ).fetchall()

    group_ids: list[int] = []
    for (raw_group_id,) in rows:
        try:
            group_ids.append(int(raw_group_id))
        except (TypeError, ValueError):
            logger.warning("Skip invalid group id in chat history: %r", raw_group_id)
    return group_ids


def load_recent_group_history(group_id: int | str, limit: int) -> list[tuple[str, int, str]]:
    if limit <= 0:
        return []

    ensure_chat_history_db()
    with sqlite3.connect(CHAT_HISTORY_DB, timeout=10) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, user_id, content
            FROM chat_logs
            WHERE group_id = ? AND content != ''
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (str(group_id), limit),
        ).fetchall()

    history: list[tuple[str, int, str]] = []
    for timestamp_text, user_id, content in reversed(rows):
        text = str(content or "").strip()
        if not text:
            continue
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            user_id_int = 0
        history.append((str(timestamp_text), user_id_int, text))
    return history


__all__ = [
    "CHAT_HISTORY_DB",
    "CHAT_HISTORY_DIR",
    "ensure_chat_history_db",
    "get_logs_for_day",
    "get_today_group_ids",
    "get_today_logs",
    "load_recent_group_history",
    "record_group_message",
]
