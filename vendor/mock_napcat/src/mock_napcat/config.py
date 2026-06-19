from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ONEBOT_PATH = "/onebot/v11/ws"
DEFAULT_SELF_ID = 2407303621
DEFAULT_GROUP_ID = 10000
DEFAULT_USER_ID = 123456789
DEFAULT_CONTROL_HOST = "127.0.0.1"
DEFAULT_CONTROL_PORT = 12716


@dataclass(frozen=True)
class MockConfig:
    project_root: Path
    env_file: Path | None
    onebot_url: str
    access_token: str
    self_id: int
    nickname: str
    default_group_id: int
    default_user_id: int
    control_host: str
    control_port: int
    bot_role: str
    strict_api: bool
    reconnect_interval: float
    heartbeat_interval: float


def find_project_root(explicit_root: str | Path | None = None) -> Path:
    if explicit_root:
        return Path(explicit_root).resolve()

    candidates = [Path.cwd(), Path(__file__).resolve()]
    for start in candidates:
        current = start if start.is_dir() else start.parent
        for parent in [current, *current.parents]:
            if (parent / "bot.py").exists() and (parent / ".env.example").exists():
                return parent.resolve()

    return Path.cwd().resolve()


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        key, separator, value = trimmed.partition("=")
        if not separator or not key.strip():
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_project_env(
    project_root: Path,
    env_file: str | Path | None = None,
) -> tuple[dict[str, str], Path | None]:
    paths: list[Path] = []
    if env_file:
        paths.append(Path(env_file))
    else:
        paths.extend([project_root / ".env.example", project_root / ".env"])

    merged: dict[str, str] = {}
    loaded_path: Path | None = None
    for path in paths:
        resolved = path if path.is_absolute() else project_root / path
        if not resolved.exists():
            continue
        merged.update(read_env_file(resolved))
        loaded_path = resolved
        if env_file:
            break

    for key, value in os.environ.items():
        if key.startswith(("MOCK_NAPCAT_", "NAPCAT_", "ONEBOT_", "HOST", "PORT", "SUPERUSERS")):
            merged[key] = value

    return merged, loaded_path


def get_value(values: dict[str, str], name: str, default: str = "") -> str:
    value = values.get(name)
    return value if value not in {None, ""} else default


def get_int(values: dict[str, str], name: str, default: int) -> int:
    raw_value = get_value(values, name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        return default


def get_float(values: dict[str, str], name: str, default: float) -> float:
    raw_value = get_value(values, name, str(default))
    try:
        return float(raw_value)
    except ValueError:
        return default


def get_bool(values: dict[str, str], name: str, default: bool = False) -> bool:
    raw_value = get_value(values, name, str(default)).strip().lower()
    if raw_value in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if raw_value in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    return default


def first_superuser(raw_value: str) -> int | None:
    value = raw_value.strip()
    if not value:
        return None

    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = value

    candidates: list[Any]
    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, int | str):
        candidates = [parsed]
    else:
        candidates = []

    if not candidates and "," in value:
        candidates = value.split(",")

    for candidate in candidates:
        try:
            return int(str(candidate).strip())
        except ValueError:
            continue
    return None


def build_onebot_url(values: dict[str, str]) -> str:
    explicit_url = get_value(values, "MOCK_NAPCAT_ONEBOT_URL")
    if explicit_url:
        return explicit_url

    host = get_value(values, "HOST", "127.0.0.1")
    port = get_int(values, "PORT", 12706)
    return f"ws://{host}:{port}{DEFAULT_ONEBOT_PATH}"


def load_config(args: Any) -> MockConfig:
    project_root = find_project_root(getattr(args, "project_root", None))
    env_values, env_path = load_project_env(project_root, getattr(args, "env_file", None))

    if onebot_url := getattr(args, "onebot_url", None):
        env_values["MOCK_NAPCAT_ONEBOT_URL"] = onebot_url
    if control_host := getattr(args, "control_host", None):
        env_values["MOCK_NAPCAT_CONTROL_HOST"] = control_host
    if control_port := getattr(args, "control_port", None):
        env_values["MOCK_NAPCAT_CONTROL_PORT"] = str(control_port)
    if self_id := getattr(args, "self_id", None):
        env_values["MOCK_NAPCAT_SELF_ID"] = str(self_id)

    superuser = first_superuser(get_value(env_values, "SUPERUSERS"))
    default_user = superuser or DEFAULT_USER_ID
    quick_account = get_value(env_values, "NAPCAT_QUICK_ACCOUNT")
    configured_self_id = get_value(env_values, "MOCK_NAPCAT_SELF_ID", quick_account)

    return MockConfig(
        project_root=project_root,
        env_file=env_path,
        onebot_url=build_onebot_url(env_values),
        access_token=get_value(env_values, "ONEBOT_ACCESS_TOKEN", "change-me"),
        self_id=int(configured_self_id) if configured_self_id else DEFAULT_SELF_ID,
        nickname=get_value(env_values, "MOCK_NAPCAT_NICKNAME", "Kanami"),
        default_group_id=get_int(env_values, "MOCK_NAPCAT_GROUP_ID", DEFAULT_GROUP_ID),
        default_user_id=get_int(env_values, "MOCK_NAPCAT_USER_ID", default_user),
        control_host=get_value(env_values, "MOCK_NAPCAT_CONTROL_HOST", DEFAULT_CONTROL_HOST),
        control_port=get_int(env_values, "MOCK_NAPCAT_CONTROL_PORT", DEFAULT_CONTROL_PORT),
        bot_role=get_value(env_values, "MOCK_NAPCAT_BOT_ROLE", "owner"),
        strict_api=bool(getattr(args, "strict_api", False))
        or get_bool(env_values, "MOCK_NAPCAT_STRICT_API", False),
        reconnect_interval=get_float(env_values, "MOCK_NAPCAT_RECONNECT_SECONDS", 3.0),
        heartbeat_interval=get_float(env_values, "MOCK_NAPCAT_HEARTBEAT_SECONDS", 30.0),
    )
