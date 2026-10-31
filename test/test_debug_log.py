from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from nonebot.log import logger

from kanamibot.core import debug_log, timing


class Message(str):
    def __new__(cls, text, timestamp):
        message = super().__new__(cls, text)
        message.record = {"time": timestamp}
        return message


@pytest.fixture
def log_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "LOG_DIR", tmp_path)
    monkeypatch.setattr(debug_log, "_sink_id", None)
    monkeypatch.setattr(debug_log, "_sink", None)
    yield tmp_path
    debug_log.configure_debug_file(False)


def test_rotation_uses_beijing_clock_hour_and_appends_after_restart(tmp_path):
    sink = debug_log.HourlyDebugLog(tmp_path)
    before = datetime(2026, 10, 31, 15, 59, tzinfo=UTC)
    after = before + timedelta(minutes=1)
    sink.write(Message("第一条\n", before))
    sink.write(Message("同一小时\n", before))
    sink.write(Message("下一小时\n", after))
    sink.stop()
    sink = debug_log.HourlyDebugLog(tmp_path)
    sink.write(Message("重启后追加\n", after))
    sink.stop()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "debug-2026-10-31_23.txt",
        "debug-2026-11-01_00.txt",
    ]
    assert (tmp_path / "debug-2026-10-31_23.txt").read_text(
        encoding="utf-8"
    ) == "第一条\n同一小时\n"
    assert (tmp_path / "debug-2026-11-01_00.txt").read_text(
        encoding="utf-8"
    ) == "下一小时\n重启后追加\n"


def test_retention_is_24_including_active_file_and_preserves_other_logs(tmp_path):
    # Include files from an earlier process, rather than testing only in-memory history.
    start = datetime(2026, 10, 1, tzinfo=debug_log.SHANGHAI)
    for index in range(24):
        name = (start + timedelta(hours=index)).strftime("debug-%Y-%m-%d_%H.txt")
        (tmp_path / name).write_text("old\n", encoding="utf-8")
    other = ["nonebot.log", "notes.txt", "debug-notes.txt", "debug-2026-10-01_00.txt.bak"]
    for name in other:
        (tmp_path / name).write_text("keep", encoding="utf-8")
    sink = debug_log.HourlyDebugLog(tmp_path)
    for index in range(24, 28):
        sink.write(Message("new\n", start + timedelta(hours=index)))
        assert len(list(tmp_path.glob("debug-????-??-??_??.txt"))) == 24
    sink.stop()
    assert not (tmp_path / "debug-2026-10-01_03.txt").exists()
    assert (tmp_path / "debug-2026-10-01_04.txt").exists()
    assert all((tmp_path / name).read_text(encoding="utf-8") == "keep" for name in other)


def test_concurrent_writes_flush_complete_records_and_stop_closes_file(tmp_path):
    sink = debug_log.HourlyDebugLog(tmp_path)
    timestamp = datetime(2026, 10, 31, 10, tzinfo=debug_log.SHANGHAI)

    def write(index):
        sink.write(Message(f"记录-{index}\n", timestamp))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(200)))
    assert (
        len((tmp_path / "debug-2026-10-31_10.txt").read_text(encoding="utf-8").splitlines()) == 200
    )
    handle = sink._file
    sink.stop()
    assert handle.closed
    # A racing writer must not reopen a stopped sink.
    sink.write(Message("late\n", timestamp))
    assert "late" not in (tmp_path / "debug-2026-10-31_10.txt").read_text(encoding="utf-8")


def test_mode_gate_no_duplicates_and_full_exception_transcript(log_directory):
    debug_log.configure_debug_file(False)
    logger.log("DEBUG", "disabled-before")
    assert list(log_directory.iterdir()) == []
    debug_log.configure_debug_file(True)
    first_id = debug_log._sink_id
    debug_log.configure_debug_file(True)
    assert debug_log._sink_id == first_id
    logger.log("TRACE", "trace-is-not-debug")
    logger.log("DEBUG", "[timing] trace=example elapsed_ms=123.456")
    logger.info("中文命令处理")
    try:
        raise ValueError("backend-failed")
    except ValueError:
        logger.exception("query failed")
    sink = debug_log._sink
    debug_log.configure_debug_file(False)
    assert sink._closed
    paths = list(log_directory.glob("*.txt"))
    assert len(paths) == 1
    content = paths[0].read_text(encoding="utf-8")
    assert content.count("[timing] trace=example") == 1
    assert "中文命令处理" in content
    assert "ValueError: backend-failed" in content
    assert "\x1b[" not in content
    assert "disabled-before" not in content
    assert "trace-is-not-debug" not in content
    logger.warning("disabled-after")
    assert paths[0].read_text(encoding="utf-8") == content
    debug_log.configure_debug_file(True)
    logger.log("DEBUG", "reopened")
    debug_log.configure_debug_file(False)
    assert len(list(log_directory.glob("*.txt"))) == 1
    assert paths[0].read_text(encoding="utf-8").startswith(content)
    assert "reopened" in paths[0].read_text(encoding="utf-8")


def test_startup_and_qq_switch_manage_sink_even_after_hooks_installed(
    log_directory,
    monkeypatch,
):
    import nonebot

    monkeypatch.setattr(timing, "_enabled", False)
    monkeypatch.setattr(timing, "_installed", True)
    monkeypatch.setattr(timing, "_normal_log_level", None)
    driver = SimpleNamespace(config=SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(nonebot, "get_driver", lambda: driver)
    monkeypatch.setattr(logger, "configure", lambda **kwargs: None)
    timing.install_debug_timing("INFO")
    assert debug_log._sink_id is None
    timing.set_debug_mode(True)
    assert debug_log._sink_id is not None
    logger.log("DEBUG", "runtime-on")
    timing.set_debug_mode(False)
    assert debug_log._sink_id is None
    assert not timing.debug_enabled()
    timing.install_debug_timing("DEBUG")
    assert debug_log._sink_id is not None
    logger.log("DEBUG", "startup-on")
    timing.install_debug_timing("INFO")
    content = next(log_directory.glob("*.txt")).read_text(encoding="utf-8")
    assert "runtime-on" in content and "startup-on" in content
