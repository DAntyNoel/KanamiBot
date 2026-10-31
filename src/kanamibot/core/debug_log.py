"""Hourly DEBUG transcripts, limited to 24 dedicated files across restarts."""

from __future__ import annotations

import re
from datetime import timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import TextIO

from nonebot.log import logger

from .paths import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
SHANGHAI = timezone(timedelta(hours=8))
_FILE_RE = re.compile(r"debug-\d{4}-\d{2}-\d{2}_\d{2}\.txt\Z")
_sink_id: int | None = None
_sink: HourlyDebugLog | None = None


class HourlyDebugLog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._file: TextIO | None = None
        self._hour: str | None = None
        self._closed = False
        self._lock = Lock()

    def write(self, message) -> None:
        timestamp = message.record["time"].astimezone(SHANGHAI)
        hour = timestamp.strftime("%Y-%m-%d_%H")
        with self._lock:
            if self._closed:
                return
            if hour != self._hour:
                if self._file is not None:
                    self._file.close()
                    self._file = None
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f"debug-{hour}.txt"
                self._file = path.open("a", encoding="utf-8", newline="")
                self._hour = hour
                # Match only our own exact filenames, never other logs or unrelated txt.
                files = sorted(
                    p
                    for p in self.directory.iterdir()
                    if _FILE_RE.fullmatch(p.name) and p.is_file()
                )
                stale = [p for p in files if p != path]
                for old in stale[: max(0, len(files) - 24)]:
                    old.unlink()
            self._file.write(str(message))
            self._file.flush()

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            if self._file is not None:
                self._file.close()
                self._file = None


def configure_debug_file(enabled: bool) -> None:
    """Persist all DEBUG-and-higher records while the bot's DEBUG mode is enabled."""
    global _sink_id, _sink
    if enabled and _sink_id is None:
        sink = HourlyDebugLog(LOG_DIR)
        _sink_id = logger.add(
            sink,
            level="DEBUG",
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS ZZ} [{level}] {name}:{function}:{line} | {message}"
            ),
            colorize=False,
            diagnose=False,
            enqueue=False,
        )
        _sink = sink
    elif not enabled and _sink_id is not None:
        logger.remove(_sink_id)
        _sink_id = None
        _sink = None
