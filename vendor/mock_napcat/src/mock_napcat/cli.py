from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from .config import load_config
from .service import run_service


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


async def smoke_send(
    config: Any,
    *,
    text: str,
    expected: str | None = None,
    role: str = "member",
    wait: float = 5,
    include_api_calls: bool = False,
) -> dict[str, Any]:
    return await control_request(
        config,
        {
            "action": "send_message",
            "message_type": "group",
            "group_id": config.default_group_id,
            "user_id": config.default_user_id,
            "role": role,
            "nickname": f"User{config.default_user_id}",
            "text": text,
            "wait_timeout": wait,
            "include_api_calls": include_api_calls,
        },
        timeout=wait + 3,
    )


async def run_smoke_command(args: argparse.Namespace) -> int:
    config = load_config(args)
    if not await wait_for_connected(config, args.connect_timeout):
        print(
            "Mock service is not connected to the OneBot backend. "
            "Start KanamiBot and mock-napcat service first.",
            file=sys.stderr,
        )
        return 1

    await control_request(config, {"action": "reset"})
    checks = [
        ("/ping", "pong", "ping"),
        ("/echo hello", "hello", "echo"),
        ("/help ping", "Ping", "help"),
    ]

    for command, expected, label in checks:
        response = await smoke_send(config, text=command, expected=expected)
        if not response.get("ok") or not replies_contain(response, expected):
            print(f"Smoke check failed: {label}", file=sys.stderr)
            print_json(response)
            return 1
        print(f"ok: {label}")

    admin_response = await smoke_send(
        config,
        text=f"/poke {config.default_user_id}",
        role="admin",
        include_api_calls=True,
    )
    api_actions = {item.get("action") for item in admin_response.get("api_calls", [])}
    if not admin_response.get("ok") or "group_poke" not in api_actions:
        print("Smoke check failed: group manager API call", file=sys.stderr)
        print_json(admin_response)
        return 1
    print("ok: group manager API call")
    return 0


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
