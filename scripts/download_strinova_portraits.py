"""Install Wiki portrait originals without starting NoneBot or NapCat."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image

REPO_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / "src" / "kanamibot" / "plugins" / "strinova"
MAX_BYTES = 24 * 1024 * 1024


def validate_image(data: bytes) -> tuple[int, int]:
    if len(data) > MAX_BYTES:
        raise ValueError("立绘超过 24 MiB")
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or min(image.size) < 400:
            raise ValueError("需要至少 400×400 的 PNG 原图")
        size = image.size
        image.verify()
    return size


def install_portrait(row: dict, output: Path, wiki_cache: Path, client: httpx.Client) -> str:
    role_id = row["id"]
    if not isinstance(role_id, int) or isinstance(role_id, bool) or role_id <= 0:
        raise ValueError("无效角色编号")
    parsed = urlparse(row["url"])
    if (
        parsed.scheme != "https"
        or parsed.netloc != "patchwiki.biligame.com"
        or not parsed.path.startswith("/images/klbq/")
        or "/thumb/" in parsed.path
        or not parsed.path.endswith(".png")
    ):
        raise ValueError("需要 Wiki 的 PNG 原图链接")
    target = output / f"{role_id}.png"
    if target.is_file():
        width, height = validate_image(target.read_bytes())
        return f"{row['name']}：已存在 {width}×{height}"
    cached = (wiki_cache / unquote(parsed.path).lstrip("/")).resolve()
    data = None
    if cached.is_relative_to(wiki_cache.resolve()) and cached.is_file():
        candidate = cached.read_bytes()
        try:
            validate_image(candidate)
            data = candidate
        except (OSError, ValueError):
            pass
    source = "复用本地" if data is not None else "下载"
    if data is None:
        data = bytearray()
        with client.stream("GET", row["url"], headers={"Referer": row["source_page"]}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("立绘超过 24 MiB")
        data = bytes(data)
    width, height = validate_image(data)
    output.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return f"{row['name']}：{source} {width}×{height}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载战绩图使用的 Wiki 高清默认立绘")
    parser.add_argument(
        "--wiki-cache",
        type=Path,
        default=REPO_DIR.parent / "local-server" / "files" / "WIKI",
        help="已下载的 Wiki 资源目录，优先复用原图",
    )
    args = parser.parse_args(argv)
    rows = json.loads((PLUGIN_DIR / "wiki_portraits.json").read_text(encoding="utf-8"))
    output = PLUGIN_DIR / "resources" / "images" / "wiki" / "portraits"
    failures = 0
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for row in rows:
            try:
                print(install_portrait(row, output, args.wiki_cache, client), flush=True)
            except (OSError, ValueError, httpx.HTTPError) as exc:
                failures += 1
                print(f"{row['name']}：失败（{exc}）", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
