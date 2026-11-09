"""Project-owned Chromium installation shared by Lens login and image search."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIR = PROJECT_ROOT / "data/image_search/chromium"


def managed_executable() -> Path | None:
    manifest = BROWSER_DIR / "browser.json"
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        executable = (BROWSER_DIR / payload["executable"]).resolve()
        if not executable.is_relative_to(BROWSER_DIR.resolve()) or not executable.is_file():
            raise ValueError
        return executable
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("托管 Chromium 不完整，请使用 --import-chromium 重新导入。") from exc


def browser_options(executable: str = "", channel: str = "") -> dict:
    """Explicit settings win; otherwise prefer our imported browser over Playwright's cache."""
    if executable:
        path = Path(executable).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            raise ValueError("配置的搜图浏览器程序不存在。")
        return {"executable_path": str(path.resolve())}
    if channel:
        return {"channel": channel}
    if managed := managed_executable():
        return {"executable_path": str(managed)}
    return {}


def profile_directory(directory: Path, selection: dict) -> Path:
    """Keep different browser installations from upgrading/downgrading the same profile."""
    if not selection:
        return directory
    identity = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()[:16]
    return directory / identity


def import_chromium(executable: Path) -> Path:
    """Copy an existing Chromium distribution, never its profile or another project's data."""
    executable = executable.expanduser().resolve()
    if (
        executable.name not in {"chrome.exe", "chrome", "chromium", "chromium.exe"}
        or not executable.is_file()
        or not (executable.parent / "resources.pak").is_file()
    ):
        raise ValueError("请指定完整 Chromium 安装目录中的 chrome.exe（或 chrome/chromium）。")
    root = BROWSER_DIR.resolve()
    source = executable.parent
    if source.is_relative_to(root) or root.is_relative_to(source):
        raise ValueError("导入来源必须在托管 Chromium 目录之外。")
    with executable.open("rb") as source_file:
        digest = hashlib.file_digest(source_file, "sha256").hexdigest()[:16]
    root.mkdir(parents=True, exist_ok=True)
    destination = root / digest
    if not destination.exists():
        # The temporary tree is always directly under our resolved managed directory.
        with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
            staged = Path(temporary) / "browser"
            shutil.copytree(source, staged)
            staged.rename(destination)
    installed = destination / executable.name
    if not installed.is_file() or not (destination / "resources.pak").is_file():
        raise ValueError("托管 Chromium 安装不完整。")
    payload = {"version": 1, "executable": installed.relative_to(root).as_posix()}
    temporary_manifest = root / f"browser.{uuid.uuid4().hex}.tmp"
    try:
        temporary_manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_manifest.replace(root / "browser.json")
    finally:
        temporary_manifest.unlink(missing_ok=True)
    return installed
