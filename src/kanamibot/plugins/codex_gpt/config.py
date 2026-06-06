from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kanamibot.core.paths import DATA_DIR

PLUGIN_DIR = Path(__file__).resolve().parent
ENV_FILE = PLUGIN_DIR / ".env"
API_KEY_FILE = PLUGIN_DIR / "apikey.env"
DEBUG_LOG_FILE = DATA_DIR / "codex_gpt" / "debug.log"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None

    key, value = line.split("=", 1)
    key = key.strip().upper()
    if not key:
        return None
    return key, _clean_env_value(value)


def _clean_env_value(value: str) -> str:
    value = _strip_inline_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value.strip()


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def _read_key_file(path: Path) -> str | None:
    if not path.exists():
        return None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip().upper()
            value = _clean_env_value(value)
            if key in {
                "CODEX_GPT_API_KEY",
                "OPENAI_API_KEY",
                "API_KEY",
                "APIKEY",
                "TOKEN",
            }:
                return value
            continue

        return _clean_env_value(line)

    return None


def _config_value(
    name: str,
    file_values: dict[str, str],
    default: str | None = None,
    *,
    env_aliases: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> str | None:
    for candidate in (name, *env_aliases):
        value = os.getenv(candidate)
        if value is not None:
            return value

    for candidate in _file_candidate_names(name, env_aliases, aliases):
        value = file_values.get(candidate)
        if value is not None:
            return value

    return default


def _nonempty_config_value(
    name: str,
    file_values: dict[str, str],
    *,
    env_aliases: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> str | None:
    for candidate in (name, *env_aliases):
        value = os.getenv(candidate)
        if value and value.strip():
            return value

    for candidate in _file_candidate_names(name, env_aliases, aliases):
        value = file_values.get(candidate)
        if value and value.strip():
            return value

    return None


def _file_candidate_names(
    name: str,
    env_aliases: tuple[str, ...],
    aliases: tuple[str, ...],
) -> tuple[str, ...]:
    names = [name, *env_aliases]
    if name.startswith("CODEX_GPT_"):
        short_name = name[len("CODEX_GPT_") :]
        names.extend([short_name, short_name.replace("_", "")])
    names.extend(aliases)
    return tuple(dict.fromkeys(candidate.upper() for candidate in names if candidate))


def _env_bool(name: str, default: bool, file_values: dict[str, str]) -> bool:
    value = _config_value(name, file_values)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, file_values: dict[str, str]) -> int:
    value = _config_value(name, file_values)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float, file_values: dict[str, str]) -> float:
    value = _config_value(name, file_values)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_optional_float(name: str, file_values: dict[str, str]) -> float | None:
    value = _config_value(name, file_values)
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _env_int_tuple(name: str, file_values: dict[str, str]) -> tuple[int, ...]:
    value = _config_value(name, file_values)
    if value is None or not value.strip():
        return ()

    items: list[int] = []
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.append(int(part))
        except ValueError:
            continue
    return tuple(items)


@dataclass(frozen=True)
class CodexGPTConfig:
    base_url: str
    api_key: str
    default_model: str
    image_model: str
    default_system_prompt: str
    temperature: float | None
    max_history_messages: int
    max_history_chars: int
    timeout_seconds: float
    image_timeout_seconds: float
    stream: bool
    session_scope: str
    auth_scheme: str
    debug: bool
    debug_log_file: Path
    image_size: str | None
    active_reply: bool
    active_reply_probability: float
    active_reply_at_probability: float
    active_reply_groups: tuple[int, ...]
    active_reply_rate_window_minutes: float
    active_reply_rate_limit: int
    active_reply_history_messages: int
    active_reply_memory_messages: int
    active_reply_max_prompt_chars: int

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    @property
    def images_generations_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/images/generations"

    @property
    def images_edits_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/images/edits"

    @property
    def auth_header(self) -> str:
        if not self.api_key:
            return ""
        if self.api_key.lower().startswith("bearer ") or self.auth_scheme == "raw":
            return self.api_key
        return f"Bearer {self.api_key}"


def load_config() -> CodexGPTConfig:
    file_values = _read_env_file(ENV_FILE)
    api_key = (
        _nonempty_config_value(
            "CODEX_GPT_API_KEY",
            file_values,
            env_aliases=("OPENAI_API_KEY",),
            aliases=("TOKEN",),
        )
        or _read_key_file(API_KEY_FILE)
        or ""
    )

    return CodexGPTConfig(
        base_url=_config_value(
            "CODEX_GPT_BASE_URL",
            file_values,
            "https://cliproxyapi-dantynoel.onrender.com/v1",
        ),
        api_key=api_key,
        default_model=_config_value("CODEX_GPT_MODEL", file_values, "gpt-5.5"),
        image_model=_config_value("CODEX_GPT_IMAGE_MODEL", file_values, "gpt-image-2"),
        default_system_prompt=_config_value(
            "CODEX_GPT_SYSTEM_PROMPT",
            file_values,
            "你是香奈美，一个可爱的群聊对话助手。回答要准确、简洁、自然；不确定时说明不确定，并主动给出可执行的下一步。",
        ),
        temperature=_env_optional_float("CODEX_GPT_TEMPERATURE", file_values),
        max_history_messages=_env_int("CODEX_GPT_MAX_HISTORY_MESSAGES", 24, file_values),
        max_history_chars=_env_int("CODEX_GPT_MAX_HISTORY_CHARS", 16000, file_values),
        timeout_seconds=_env_float("CODEX_GPT_TIMEOUT", 120.0, file_values),
        image_timeout_seconds=_env_float("CODEX_GPT_IMAGE_TIMEOUT", 300.0, file_values),
        stream=_env_bool("CODEX_GPT_STREAM", True, file_values),
        session_scope=_config_value("CODEX_GPT_SESSION_SCOPE", file_values, "user").strip().lower(),
        auth_scheme=_config_value("CODEX_GPT_AUTH_SCHEME", file_values, "bearer").strip().lower(),
        debug=_env_bool("CODEX_GPT_DEBUG", False, file_values),
        debug_log_file=Path(_config_value("CODEX_GPT_DEBUG_LOG", file_values, str(DEBUG_LOG_FILE))),
        image_size=_config_value("CODEX_GPT_IMAGE_SIZE", file_values) or None,
        active_reply=_env_bool("CODEX_GPT_ACTIVE_REPLY", False, file_values),
        active_reply_probability=max(
            0.0, min(1.0, _env_float("CODEX_GPT_ACTIVE_REPLY_PROBABILITY", 0.03, file_values))
        ),
        active_reply_at_probability=max(
            0.0, min(1.0, _env_float("CODEX_GPT_ACTIVE_REPLY_AT_PROBABILITY", 0.95, file_values))
        ),
        active_reply_groups=_env_int_tuple("CODEX_GPT_ACTIVE_REPLY_GROUPS", file_values),
        active_reply_rate_window_minutes=max(
            0.1, _env_float("CODEX_GPT_ACTIVE_REPLY_RATE_WINDOW_MINUTES", 5.0, file_values)
        ),
        active_reply_rate_limit=max(
            1, _env_int("CODEX_GPT_ACTIVE_REPLY_RATE_LIMIT", 100, file_values)
        ),
        active_reply_history_messages=max(
            0, _env_int("CODEX_GPT_ACTIVE_REPLY_HISTORY_MESSAGES", 80, file_values)
        ),
        active_reply_memory_messages=max(
            1, _env_int("CODEX_GPT_ACTIVE_REPLY_MEMORY_MESSAGES", 20, file_values)
        ),
        active_reply_max_prompt_chars=max(
            500, _env_int("CODEX_GPT_ACTIVE_REPLY_MAX_PROMPT_CHARS", 6000, file_values)
        ),
    )
