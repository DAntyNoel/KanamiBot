from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Any

from .config import CodexGPTConfig


class CodexGPTDebugLogger:
    def __init__(self, config: CodexGPTConfig):
        self.enabled = config.debug
        self._api_key = config.api_key
        self._auth_header = config.auth_header
        self._logger = logging.getLogger("kanami.codex_gpt.debug")

        if not self.enabled:
            return

        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.handlers.clear()
        try:
            config.debug_log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                config.debug_log_file,
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self._logger.addHandler(handler)
        except Exception as exc:
            self.enabled = False
            logging.getLogger("kanami.codex_gpt").warning("codex_gpt debug log disabled: %s", exc)

    def log(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self._logger.debug("%s %s", event, self._dump(fields))

    def exception(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self._logger.exception("%s %s", event, self._dump(fields))

    def _dump(self, fields: dict[str, Any]) -> str:
        safe_fields = _sanitize(fields, api_key=self._api_key, auth_header=self._auth_header)
        return json.dumps(safe_fields, ensure_ascii=False, default=str)


def _sanitize(value: Any, api_key: str, auth_header: str) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"authorization", "api_key", "apikey", "token"}:
                safe[key_text] = "***"
            else:
                safe[key_text] = _sanitize(item, api_key=api_key, auth_header=auth_header)
        return safe

    if isinstance(value, list):
        return [_sanitize(item, api_key=api_key, auth_header=auth_header) for item in value]

    if isinstance(value, str):
        text = value
        if api_key:
            text = text.replace(api_key, "***")
        if auth_header:
            text = text.replace(auth_header, "***")
        return text

    return value
