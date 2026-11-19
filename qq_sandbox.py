from __future__ import annotations

import argparse
import re
from pathlib import Path

import nonebot
from nonebot.log import logger

from kanamibot.qqbot.adapter import QQAdapter

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


def create_app(
    credentials: Path,
    port: int = 12710,
    websocket: bool = True,
    *,
    production: bool = False,
) -> None:
    app_id, secret = read_credentials(credentials)
    nonebot.init(
        _env_file=None,
        driver="~fastapi+~httpx+~websockets",
        host="127.0.0.1",
        port=port,
        log_level="INFO",
        command_start={"", "/", "#"},
        qq_api_base=(
            "https://api.bot.qq.com/" if production else "https://sandbox.api.sgroup.qq.com/"
        ),
        qq_verify_webhook=True,
        qq_bots=[{
            "id": app_id,
            "secret": secret,
            "use_websocket": websocket,
            "intent": {"c2c_group_at_messages": True},
        }],
    )
    nonebot.get_driver().register_adapter(QQAdapter)
    for plugin in ("kanamibot.plugins.ping", "kanamibot.qqbot.permissions"):
        if nonebot.load_plugin(plugin) is None:
            raise RuntimeError(f"无法加载插件：{plugin}")


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ 官方机器人独立沙箱（ping/pong 和群权限提醒）")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--port", type=int, default=12710)
    parser.add_argument("--webhook", action="store_true", help="改用已配置公网回调的 Webhook")
    parser.add_argument("--production", action="store_true", help="接收实际邀请群的正式环境事件")
    args = parser.parse_args()
    create_app(args.credentials, args.port, not args.webhook, production=args.production)
    log_path = PROJECT_ROOT / "logs" / "qq_sandbox.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_path, level="INFO", rotation="5 MB", retention="3 days", encoding="utf-8")
    logger.info("QQBot 连接环境：{}", "正式环境" if args.production else "沙箱环境")
    nonebot.run()


if __name__ == "__main__":
    main()
