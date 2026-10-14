from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .client import Settings
from .query import QueryError, add_query_arguments, query_from_args
from .render import render_html, render_png
from .service import QUERY_TIMEOUT_SECONDS, create_report

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查询卡拉彼丘玩家并保存战绩 PNG，不启动 Bot。")
    add_query_arguments(parser)
    parser.add_argument("-o", "--output", type=Path, help="PNG 保存路径")
    parser.add_argument("--html-output", type=Path, help="同时保存可在浏览器中查看的 HTML")
    parser.add_argument("--api-url", help="覆盖 STRINOVA_API_URL")
    parser.add_argument("--timeout", type=float, help="覆盖 API 超时，单位秒")
    parser.add_argument("--browser-channel", choices=("chrome", "msedge"))
    parser.add_argument("--browser-executable", help="浏览器可执行文件路径")
    return parser


async def run(args: argparse.Namespace) -> Path:
    query = query_from_args(args)
    requested_output = args.output.expanduser().resolve() if args.output else None
    html_output = args.html_output.expanduser().resolve() if args.html_output else None
    if requested_output and requested_output.suffix.lower() != ".png":
        raise QueryError("图片输出路径必须使用 .png 后缀。")
    if html_output and html_output.suffix.lower() not in (".html", ".htm"):
        raise QueryError("HTML 输出路径必须使用 .html 或 .htm 后缀。")
    if requested_output is not None and requested_output == html_output:
        raise QueryError("HTML 和 PNG 输出路径不能相同。")
    configured = Settings.from_env()
    settings = Settings(
        base_url=args.api_url or configured.base_url,
        timeout=args.timeout if args.timeout is not None else configured.timeout,
        browser_channel=args.browser_channel or configured.browser_channel,
        browser_executable=args.browser_executable or configured.browser_executable,
        avatar_url_template=configured.avatar_url_template,
    )
    print(f"正在查询「{query.target}」……", flush=True)
    async with asyncio.timeout(QUERY_TIMEOUT_SECONDS):
        report = await create_report(query, settings)
        image = await render_png(report, settings)
    output = requested_output or (
        PROJECT_ROOT
        / "data"
        / "strinova"
        / f"{report.player_id}-{query.view}-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
    )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    if html_output:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(render_html(report), encoding="utf-8")
        print(f"HTML 已保存：{html_output}")
    for warning in report.warnings:
        print(f"部分数据不可用：{warning}", file=sys.stderr)
    return output


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if not args.nickname and args.nick is None and args.player_id is None:
            args.nickname = [input("请输入玩家昵称或 ID：").strip()]
        output = asyncio.run(run(args))
    except TimeoutError:
        print("查询失败：战绩查询已超过 2 分钟，已取消本次查询，请稍后重试。", file=sys.stderr)
        return 2
    except (QueryError, OSError, EOFError) as exc:
        print(f"查询失败：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("查询已取消。", file=sys.stderr)
        return 130
    print(f"图片已保存：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
