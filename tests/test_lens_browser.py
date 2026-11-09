from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanamibot import lens_browser


def distribution(path: Path, content: bytes = b"chromium") -> Path:
    path.mkdir()
    executable = path / "chrome.exe"
    executable.write_bytes(content)
    (path / "resources.pak").write_bytes(b"resources")
    (path / "locales").mkdir()
    (path / "locales/zh-CN.pak").write_bytes(b"locale")
    return executable


def test_import_keeps_source_and_owns_complete_distribution(tmp_path, monkeypatch):
    source = distribution(tmp_path / "shared-browser")
    root = tmp_path / "kanamibot-browser"
    monkeypatch.setattr(lens_browser, "BROWSER_DIR", root)
    installed = lens_browser.import_chromium(source)
    assert installed.is_relative_to(root)
    assert (installed.parent / "locales/zh-CN.pak").read_bytes() == b"locale"
    assert lens_browser.import_chromium(source) == installed
    # Moving the shared source does not break the project-owned installation.
    source.parent.rename(tmp_path / "source-moved")
    assert lens_browser.managed_executable() == installed
    assert lens_browser.browser_options() == {"executable_path": str(installed)}
    assert installed.read_bytes() == b"chromium"


def test_failed_import_preserves_previous_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(lens_browser, "BROWSER_DIR", tmp_path / "managed")
    previous = lens_browser.import_chromium(distribution(tmp_path / "old"))
    source = distribution(tmp_path / "new", b"new-version")

    def fail_copy(*args, **kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(lens_browser.shutil, "copytree", fail_copy)
    with pytest.raises(OSError):
        lens_browser.import_chromium(source)
    assert lens_browser.managed_executable() == previous
    assert not list(lens_browser.BROWSER_DIR.glob(".import-*"))


def test_explicit_selection_and_relative_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(lens_browser, "BROWSER_DIR", tmp_path / "managed")
    monkeypatch.setattr(lens_browser, "PROJECT_ROOT", tmp_path)
    source = distribution(tmp_path / "explicit")
    assert lens_browser.browser_options() == {}
    lens_browser.import_chromium(source)
    assert lens_browser.browser_options(channel="msedge") == {"channel": "msedge"}
    assert lens_browser.browser_options("explicit/chrome.exe", "msedge") == {
        "executable_path": str(source.resolve())
    }
    with pytest.raises(ValueError, match="不存在"):
        lens_browser.browser_options("missing.exe")


@pytest.mark.parametrize("target", ["../outside/chrome.exe", "missing.exe"])
def test_invalid_managed_install_does_not_silently_fall_back(tmp_path, monkeypatch, target):
    root = tmp_path / "managed"
    root.mkdir()
    monkeypatch.setattr(lens_browser, "BROWSER_DIR", root)
    distribution(tmp_path / "outside")
    (root / "browser.json").write_text(json.dumps({"executable": target}), encoding="utf-8")
    with pytest.raises(ValueError, match="重新导入"):
        lens_browser.browser_options()


def test_import_rejects_incomplete_and_recursive_sources(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    monkeypatch.setattr(lens_browser, "BROWSER_DIR", root)
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="完整"):
        lens_browser.import_chromium(executable)
    root.mkdir()
    with pytest.raises(ValueError, match="目录之外"):
        lens_browser.import_chromium(distribution(root / "nested"))


def test_browser_versions_and_login_query_profiles_stay_separate(tmp_path):
    login = tmp_path / "browser-login"
    query = tmp_path / "browser"
    old = {"executable_path": "chromium-130/chrome.exe"}
    new = {"executable_path": "chromium-154/chrome.exe"}
    assert lens_browser.profile_directory(login, {}) == login
    assert lens_browser.profile_directory(login, old).parent == login
    assert lens_browser.profile_directory(login, old) != lens_browser.profile_directory(login, new)
    assert lens_browser.profile_directory(login, old) != lens_browser.profile_directory(query, old)
