"""Migrate plugin databases with the same entry point and .env as KanamiBot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移或检查 KanamiBot 森空岛数据库")
    parser.add_argument("command", choices=("upgrade", "check", "current", "heads"))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    args = parser.parse_args()

    from bot import create_app

    create_app(args.env_file.resolve())

    from nonebot import get_plugin_by_module_name

    if get_plugin_by_module_name("nonebot_plugin_skland") is None:
        raise RuntimeError("森空岛插件未成功加载，请先检查依赖与 KANAMIBOT_SKLAND_ENABLED")

    from nonebot_plugin_orm.__main__ import main as orm_main

    orm_main(args=[args.command], prog_name="migrate_skland", standalone_mode=False)


if __name__ == "__main__":
    main()
