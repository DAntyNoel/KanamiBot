from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path
from typing import Any

from .config import load_config
from .service import run_service

DEFAULT_CHECK_DIR = "test/check"
DEFAULT_REPORT_PATH = "data/check_reports/mock_napcat.html"
STATE_SNAPSHOT_PATHS = (
    "data/group_manager.json",
    "data/plugin_configs/bilibili.json",
    "data/codex_gpt/sessions.json",
    "data/advanced_media",
    "data/majsoul/majsoul.sqlite",
    "data/majsoul/majsoul.sqlite-wal",
    "data/majsoul/majsoul.sqlite-shm",
)


@dataclass(slots=True)
class CheckCase:
    source: str
    line_no: int
    command: str
    expected: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    skip_reason: str = ""

    @property
    def label(self) -> str:
        return str(self.options.get("label") or f"{self.source}:{self.line_no}")


@dataclass(slots=True)
class CheckResult:
    case: CheckCase
    status: str
    duration: float = 0.0
    actual: str = ""
    error: str = ""
    response: dict[str, Any] | None = None


@dataclass(slots=True)
class SnapshotItem:
    target: Path
    snapshot: Path
    existed: bool
    is_dir: bool


class StateSnapshot:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="mock-napcat-check-")
        self.temp_root = Path(self._temp_dir.name)
        self.items: list[SnapshotItem] = []

    def capture(self) -> None:
        for index, relative_path in enumerate(STATE_SNAPSHOT_PATHS):
            target = (self.project_root / relative_path).resolve()
            if not _is_relative_to(target, self.project_root):
                raise RuntimeError(f"refusing to snapshot outside project root: {target}")

            snapshot = self.temp_root / str(index)
            existed = target.exists()
            is_dir = target.is_dir() if existed else False
            self.items.append(
                SnapshotItem(target=target, snapshot=snapshot, existed=existed, is_dir=is_dir)
            )
            if not existed:
                continue
            if is_dir:
                shutil.copytree(target, snapshot)
            else:
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, snapshot)

    def restore(self) -> None:
        for item in reversed(self.items):
            if item.target.exists():
                if item.target.is_dir():
                    shutil.rmtree(item.target)
                else:
                    item.target.unlink()
            if not item.existed:
                continue
            item.target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir:
                shutil.copytree(item.snapshot, item.target)
            else:
                shutil.copy2(item.snapshot, item.target)

    def cleanup(self) -> None:
        self._temp_dir.cleanup()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", help="KanamiBot project root. Defaults to auto-detect.")
    parser.add_argument("--env-file", help="Environment file relative to project root.")
    parser.add_argument("--control-host", help="Mock control host. Defaults to 127.0.0.1.")
    parser.add_argument("--control-port", type=int, help="Mock control port. Defaults to 12716.")


async def control_request(
    config: Any,
    payload: dict[str, Any],
    timeout: float = 10,
) -> dict[str, Any]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(config.control_host, config.control_port),
        timeout=timeout,
    )
    writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    writer.close()
    await writer.wait_closed()
    if not line:
        return {"ok": False, "error": "mock service closed the control connection"}
    return json.loads(line.decode("utf-8"))


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _is_relative_to(path: Path, parent: Path) -> bool:
    with contextlib.suppress(ValueError):
        path.relative_to(parent)
        return True
    return False


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def parse_bool(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "enable", "enabled"}


def parse_int_list(value: object) -> list[int]:
    result: list[int] = []
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


def parse_str_list(value: object) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parse_case_options(raw_options: str) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if not raw_options.strip():
        return options

    for token in shlex.split(raw_options):
        if "=" not in token:
            if token.lower() == "private":
                options["private"] = True
            continue

        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"user", "group", "reply"}:
            try:
                options[key] = int(value)
            except ValueError:
                options[key] = value
        elif key == "wait":
            try:
                options[key] = float(value)
            except ValueError:
                options[key] = value
        elif key == "private":
            options[key] = parse_bool(value)
        elif key == "at":
            options.setdefault("at", []).extend(parse_int_list(value))
        elif key == "image":
            options.setdefault("image", []).extend(parse_str_list(value))
        else:
            options[key] = value
    return options


def parse_check_line(raw_line: str, source: str, line_no: int) -> CheckCase | None:
    line = raw_line.strip("\ufeff\r\n")
    if not line.strip() or line.lstrip().startswith("//"):
        return None

    if "\t" in line:
        fields = line.split("\t")
        head = fields[0].strip()
        if head.upper() == "SKIP":
            command = fields[1].strip() if len(fields) > 1 else ""
            reason = fields[2].strip() if len(fields) > 2 else "skipped by check file"
            return CheckCase(source=source, line_no=line_no, command=command, skip_reason=reason)

        command = head
        expected = fields[1].strip() if len(fields) > 1 else ""
        raw_options = " ".join(field.strip() for field in fields[2:] if field.strip())
        return CheckCase(
            source=source,
            line_no=line_no,
            command=command,
            expected=expected,
            options=parse_case_options(raw_options),
        )

    parts = line.split(maxsplit=1)
    command = parts[0].strip()
    expected = parts[1].strip() if len(parts) > 1 else ""
    return CheckCase(source=source, line_no=line_no, command=command, expected=expected)


def load_check_cases(check_dir: Path) -> list[CheckCase]:
    cases: list[CheckCase] = []
    if not check_dir.exists():
        return cases

    for path in sorted(check_dir.glob("*.txt")):
        source = str(path.relative_to(check_dir.parent))
        for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            case = parse_check_line(raw_line, source, line_no)
            if case:
                cases.append(case)
    return cases


def format_message(item: dict[str, Any]) -> str:
    destination = ""
    if item.get("message_type") == "group":
        destination = f"group {item.get('group_id')}"
    else:
        destination = f"user {item.get('user_id')}"
    return f"#{item.get('message_id')} [{destination}] {item.get('raw_message', '')}"


async def run_service_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
    )
    print(f"Mock NapCat control: {config.control_host}:{config.control_port}")
    print(f"OneBot reverse WebSocket: {config.onebot_url}")
    print(f"Self ID: {config.self_id}")
    await run_service(config)
    return 0


async def run_send_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    message = " ".join(args.message).strip()
    wait_timeout = 0 if args.no_wait else args.wait
    payload = {
        "action": "send_message",
        "message_type": "private" if args.private else "group",
        "group_id": args.group or config.default_group_id,
        "user_id": args.user or config.default_user_id,
        "role": args.role,
        "nickname": args.nickname,
        "text": message,
        "reply_id": args.reply,
        "at": args.at or [],
        "image_urls": args.image or [],
        "wait_timeout": wait_timeout,
        "include_api_calls": args.include_api_calls,
    }
    response = await control_request(config, payload)
    if args.json:
        print_json(response)
    elif response.get("ok"):
        print(f"Sent message_id={response.get('event_message_id')}")
        replies = response.get("replies") or []
        if replies:
            print("Replies:")
            for reply in replies:
                print(f"  {format_message(reply)}")
        else:
            print("No reply captured.")
    else:
        print(f"Mock send failed: {response.get('error')}", file=sys.stderr)
    return 0 if response.get("ok") else 1


async def run_history_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    response = await control_request(config, {"action": "history", "limit": args.limit})
    if args.json:
        print_json(response)
    elif response.get("ok"):
        for item in response.get("messages", []):
            print(format_message(item))
    else:
        print(f"Mock history failed: {response.get('error')}", file=sys.stderr)
    return 0 if response.get("ok") else 1


async def run_calls_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    response = await control_request(config, {"action": "api_calls", "limit": args.limit})
    if args.json:
        print_json(response)
    elif response.get("ok"):
        for item in response.get("api_calls", []):
            print(f"#{item.get('sequence')} {item.get('action')} {item.get('params')}")
    else:
        print(f"Mock calls failed: {response.get('error')}", file=sys.stderr)
    return 0 if response.get("ok") else 1


async def wait_for_connected(config: Any, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            response = await control_request(config, {"action": "status"}, timeout=3)
        except OSError:
            await asyncio.sleep(0.5)
            continue
        if response.get("ok") and response.get("connected"):
            return True
        await asyncio.sleep(0.5)
    return False


def replies_contain(response: dict[str, Any], expected: str) -> bool:
    return any(
        expected in str(reply.get("raw_message", ""))
        for reply in response.get("replies", [])
    )


def joined_reply_text(response: dict[str, Any]) -> str:
    return "\n".join(
        str(reply.get("raw_message", ""))
        for reply in response.get("replies", [])
    )


def compact_json(payload: Any, limit: int = 4000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... <truncated>"


def option_float(options: dict[str, Any], name: str, default: float) -> float:
    value = options.get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def run_check_case(config: Any, case: CheckCase, default_timeout: float) -> CheckResult:
    if case.skip_reason:
        return CheckResult(case=case, status="skip", error=case.skip_reason)

    wait_timeout = option_float(case.options, "wait", default_timeout)
    user_id = case.options.get("user", config.default_user_id)
    group_id = case.options.get("group", config.default_group_id)
    message_type = "private" if parse_bool(case.options.get("private", False)) else "group"
    payload = {
        "action": "send_message",
        "message_type": message_type,
        "group_id": group_id,
        "user_id": user_id,
        "role": str(case.options.get("role") or "member"),
        "nickname": str(case.options.get("nickname") or f"User{user_id}"),
        "text": case.command,
        "reply_id": case.options.get("reply"),
        "at": case.options.get("at") or [],
        "image_urls": case.options.get("image") or [],
        "wait_timeout": wait_timeout,
        "include_api_calls": bool(case.options.get("include_api_calls")),
    }

    started = time.perf_counter()
    try:
        response = await control_request(config, payload, timeout=wait_timeout + 3)
    except Exception as exc:
        return CheckResult(
            case=case,
            status="fail",
            duration=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    actual = joined_reply_text(response)
    duration = time.perf_counter() - started
    if not response.get("ok"):
        return CheckResult(
            case=case,
            status="fail",
            duration=duration,
            actual=actual,
            error=str(response.get("error") or "mock service returned ok=false"),
            response=response,
        )

    if case.expected and case.expected not in actual:
        return CheckResult(
            case=case,
            status="fail",
            duration=duration,
            actual=actual,
            error="expected string was not found in bot replies",
            response=response,
        )

    return CheckResult(
        case=case,
        status="pass",
        duration=duration,
        actual=actual,
        response=response,
    )


def status_counts(results: list[CheckResult]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def status_label(status: str) -> str:
    return {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}.get(status, status.upper())


def render_result_row(result: CheckResult) -> str:
    case = result.case
    response = compact_json(result.response) if result.response is not None else ""
    return (
        f"<tr class='{html_escape(result.status)}'>"
        f"<td>{html_escape(status_label(result.status))}</td>"
        f"<td>{html_escape(case.label)}</td>"
        f"<td><code>{html_escape(case.command)}</code></td>"
        f"<td>{html_escape(case.expected)}</td>"
        f"<td>{result.duration:.2f}s</td>"
        f"<td><pre>{html_escape(result.actual)}</pre></td>"
        f"<td><pre>{html_escape(result.error)}</pre></td>"
        f"<td><details><summary>JSON</summary><pre>{html_escape(response)}</pre></details></td>"
        "</tr>"
    )


def write_html_report(
    *,
    report_path: Path,
    config: Any,
    check_dir: Path,
    results: list[CheckResult],
    started_at: float,
    connected: bool,
    note: str = "",
) -> None:
    finished_at = time.time()
    counts = status_counts(results)
    total = len(results)
    duration = finished_at - started_at
    env_rows = {
        "Project root": str(config.project_root),
        "Check dir": str(check_dir),
        "Report": str(report_path),
        "OneBot URL": config.onebot_url,
        "Control": f"{config.control_host}:{config.control_port}",
        "Self ID": str(config.self_id),
        "Default group": str(config.default_group_id),
        "Default user": str(config.default_user_id),
        "Connected": str(connected),
    }
    env_table = "\n".join(
        f"<tr><th>{html_escape(key)}</th><td>{html_escape(value)}</td></tr>"
        for key, value in env_rows.items()
    )
    rows = "\n".join(render_result_row(result) for result in results)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Mock NapCat Check Report</title>
  <style>
    body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 24px; color: #17202a; }}
    h1 {{ margin-bottom: 8px; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }}
    .pill {{ border-radius: 999px; padding: 6px 12px; font-weight: 600; }}
    .pass .pill, .pill.pass {{ background: #d9f2e3; color: #0b6b36; }}
    .fail .pill, .pill.fail {{ background: #fde1df; color: #9f1d16; }}
    .skip .pill, .pill.skip {{ background: #eceff3; color: #4a5568; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #d7dce2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f8; text-align: left; }}
    tr.pass td:first-child {{ color: #0b6b36; font-weight: 700; }}
    tr.fail td:first-child {{ color: #9f1d16; font-weight: 700; }}
    tr.skip td:first-child {{ color: #4a5568; font-weight: 700; }}
    code {{ white-space: pre-wrap; }}
    pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; max-width: 520px; }}
    details summary {{ cursor: pointer; color: #2f5f9f; }}
  </style>
</head>
<body>
  <h1>Mock NapCat Check Report</h1>
  <p>Generated at {html_escape(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_at)))}.
  Duration: {duration:.2f}s.</p>
  <div class="summary">
    <span class="pill">Total {total}</span>
    <span class="pill pass">Pass {counts.get("pass", 0)}</span>
    <span class="pill fail">Fail {counts.get("fail", 0)}</span>
    <span class="pill skip">Skip {counts.get("skip", 0)}</span>
  </div>
  <h2>Environment</h2>
  <table>{env_table}</table>
  {f"<h2>Note</h2><p>{html_escape(note)}</p>" if note else ""}
  <h2>Cases</h2>
  <table>
    <thead>
      <tr>
        <th>Status</th><th>Case</th><th>Command</th><th>Expected</th>
        <th>Time</th><th>Actual replies</th><th>Error / reason</th><th>Raw response</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def connection_failure_result(message: str) -> CheckResult:
    return CheckResult(
        case=CheckCase(source="connection", line_no=0, command="connect", expected="connected"),
        status="fail",
        error=message,
    )


async def run_smoke_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    project_root = Path(config.project_root)
    check_dir = resolve_project_path(project_root, args.check_dir).resolve()
    report_path = resolve_project_path(project_root, args.report).resolve()
    started_at = time.time()
    cases = load_check_cases(check_dir)

    connected = await wait_for_connected(config, args.connect_timeout)
    if not connected:
        note = (
            "Mock service is not connected to the OneBot backend. "
            "Start KanamiBot and mock-napcat service first."
        )
        results = [connection_failure_result(note)]
        results.extend(
            CheckResult(case=case, status="skip", error="connection was not available")
            for case in cases
        )
        write_html_report(
            report_path=report_path,
            config=config,
            check_dir=check_dir,
            results=results,
            started_at=started_at,
            connected=False,
            note=note,
        )
        print(note, file=sys.stderr)
        print(f"Report: {report_path}", file=sys.stderr)
        return 1

    if not cases:
        note = f"No check cases found in {check_dir}"
        results = [connection_failure_result(note)]
        write_html_report(
            report_path=report_path,
            config=config,
            check_dir=check_dir,
            results=results,
            started_at=started_at,
            connected=True,
            note=note,
        )
        print(note, file=sys.stderr)
        print(f"Report: {report_path}", file=sys.stderr)
        return 1

    snapshot = StateSnapshot(project_root)
    snapshot.capture()
    results: list[CheckResult] = []
    try:
        await control_request(config, {"action": "reset"})
        for case in cases:
            result = await run_check_case(config, case, args.case_timeout)
            results.append(result)
            print(f"{status_label(result.status).lower()}: {case.label} {case.command}")
            if result.status == "fail":
                print(f"  expected: {case.expected}", file=sys.stderr)
                print(f"  actual: {result.actual}", file=sys.stderr)
                print(f"  error: {result.error}", file=sys.stderr)
        await control_request(config, {"action": "reset"})
    finally:
        snapshot.restore()
        snapshot.cleanup()

    write_html_report(
        report_path=report_path,
        config=config,
        check_dir=check_dir,
        results=results,
        started_at=started_at,
        connected=True,
    )
    counts = status_counts(results)
    print(
        "Summary: "
        f"pass={counts.get('pass', 0)} "
        f"fail={counts.get('fail', 0)} "
        f"skip={counts.get('skip', 0)}"
    )
    print(f"Report: {report_path}")
    return 1 if counts.get("fail", 0) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-napcat")
    subparsers = parser.add_subparsers(dest="command", required=True)

    service_parser = subparsers.add_parser("service", help="Run the mock OneBot backend.")
    add_common_args(service_parser)
    service_parser.add_argument("--onebot-url", help="Override the OneBot reverse WebSocket URL.")
    service_parser.add_argument("--self-id", type=int, help="Mock bot QQ number.")
    service_parser.add_argument(
        "--strict-api",
        action="store_true",
        help="Fail unknown API calls.",
    )
    service_parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logs.")
    service_parser.set_defaults(func=run_service_command)

    send_parser = subparsers.add_parser(
        "send",
        help="Inject one message into the running service.",
    )
    add_common_args(send_parser)
    send_parser.add_argument(
        "message",
        nargs=argparse.REMAINDER,
        help="Message text or CQ text.",
    )
    send_parser.add_argument(
        "--private",
        action="store_true",
        help="Send a private message event.",
    )
    send_parser.add_argument("--group", type=int, help="Group ID for group messages.")
    send_parser.add_argument("--user", type=int, help="Sender user ID.")
    send_parser.add_argument("--role", choices=["member", "admin", "owner"], default="member")
    send_parser.add_argument("--nickname", default="", help="Sender nickname.")
    send_parser.add_argument("--reply", type=int, help="Prepend a reply segment.")
    send_parser.add_argument("--at", action="append", default=[], help="Prepend an at segment.")
    send_parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Append an image URL segment.",
    )
    send_parser.add_argument(
        "--wait",
        type=float,
        default=3.0,
        help="Seconds to wait for replies.",
    )
    send_parser.add_argument("--no-wait", action="store_true", help="Do not wait for bot replies.")
    send_parser.add_argument("--include-api-calls", action="store_true")
    send_parser.add_argument("--json", action="store_true", help="Print raw JSON response.")
    send_parser.set_defaults(func=run_send_command)

    smoke_parser = subparsers.add_parser("smoke", help="Run end-to-end validation.")
    add_common_args(smoke_parser)
    smoke_parser.add_argument("--connect-timeout", type=float, default=10.0)
    smoke_parser.add_argument(
        "--check-dir",
        default=DEFAULT_CHECK_DIR,
        help=f"Directory containing check .txt files. Defaults to {DEFAULT_CHECK_DIR}.",
    )
    smoke_parser.add_argument(
        "--report",
        default=DEFAULT_REPORT_PATH,
        help=f"HTML report path. Defaults to {DEFAULT_REPORT_PATH}.",
    )
    smoke_parser.add_argument(
        "--case-timeout",
        type=float,
        default=6.0,
        help="Seconds to wait for bot replies for each case unless overridden.",
    )
    smoke_parser.set_defaults(func=run_smoke_command)

    history_parser = subparsers.add_parser("history", help="Show captured bot messages.")
    add_common_args(history_parser)
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--json", action="store_true")
    history_parser.set_defaults(func=run_history_command)

    calls_parser = subparsers.add_parser("calls", help="Show captured OneBot API calls.")
    add_common_args(calls_parser)
    calls_parser.add_argument("--limit", type=int, default=20)
    calls_parser.add_argument("--json", action="store_true")
    calls_parser.set_defaults(func=run_calls_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"mock-napcat failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
