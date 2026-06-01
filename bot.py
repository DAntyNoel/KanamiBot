from __future__ import annotations

import argparse
import os
from pathlib import Path

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
PLUGIN_DIR = PROJECT_ROOT / "src" / "kanamibot" / "plugins"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KanamiBot NoneBot2 entry point")
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to the environment file loaded by NoneBot.",
    )
    return parser.parse_known_args()[0]


def create_app(env_file: str | Path = DEFAULT_ENV_FILE) -> None:
    os.chdir(PROJECT_ROOT)

    target_env_file = Path(env_file)
    if target_env_file.exists():
        load_dotenv(dotenv_path=target_env_file, override=True)

    nonebot.init(_env_file=str(target_env_file))

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    nonebot.load_plugins(str(PLUGIN_DIR.relative_to(PROJECT_ROOT)))


def main() -> None:
    args = parse_args()
    create_app(args.env_file)
    nonebot.run()


if __name__ == "__main__":
    main()
