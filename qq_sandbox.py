from __future__ import annotations

import argparse
import re
from pathlib import Path

import nonebot
from nonebot.adapters.qq import Adapter
from nonebot.log import logger

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS = Path.home() / "Downloads" / "qqbot.txt"


def read_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    for line in filter(None, lines):
        match = re.fullmatch(r"(appid|appsecret|secret)\s*[:：=\s]\s*(\S+)", line, re.I)
        if match:
            key = "id" if match[1].lower() == "appid" else "secret"
            values[key] = match[2].strip("\"'")
        elif line.isdecimal() and "id" not in values:
            values["id"] = line
        elif "secret" not in values and re.fullmatch(r"[A-Za-z0-9_-]+", line):
            values["secret"] = line
    if not values.get("id", "").isdecimal() or not values.get("secret"):
        raise ValueError("凭据文件需要 AppID 和 Secret，可使用 appid:值 / secret:值两行格式")
    return values["id"], values["secret"]


def create_app(credentials: Path, port: int = 12710, websocket: bool = True) -> None:
    app_id, secret = read_credentials(credentials)
    nonebot.init(
        _env_file=None,
        driver="~fastapi+~httpx+~websockets",
        host="127.0.0.1",
        port=port,
        log_level="INFO",
        command_start={"", "/", "#"},
        qq_api_base="https://sandbox.api.sgroup.qq.com/",
        qq_verify_webhook=True,
        qq_bots=[{
            "id": app_id,
            "secret": secret,
            "use_websocket": websocket,
            "intent": {"c2c_group_at_messages": True},
        }],
    )
    nonebot.get_driver().register_adapter(Adapter)
    if nonebot.load_plugin("kanamibot.plugins.ping") is None:
        raise RuntimeError("无法加载 ping 插件")


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ 官方机器人独立沙箱（仅 ping/pong）")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--port", type=int, default=12710)
    parser.add_argument("--webhook", action="store_true", help="改用已配置公网回调的 Webhook")
    args = parser.parse_args()
    create_app(args.credentials, args.port, not args.webhook)
    log_path = PROJECT_ROOT / "logs" / "qq_sandbox.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_path, level="INFO", rotation="5 MB", retention="3 days", encoding="utf-8")
    nonebot.run()


if __name__ == "__main__":
    main()
