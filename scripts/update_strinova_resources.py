"""Import a verified kqhub resource checkout without starting Bot or NapCat."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from PIL import Image

REPO_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / "src" / "kanamibot" / "plugins" / "strinova"
UPSTREAM = "https://github.com/kqhub-win/calabiyau-resource"
IMAGE_FIELDS = {
    "avatars": ("IconItem", "IconDisplayItem"),
    "characters": ("IconRoleHud", "CareerIcon"),
    "maps": ("IconMapIntro", "IconMapCareer", "IconMapCareerTitle"),
    "ranks": ("IconDivision", "IconDivisionLevel"),
}


def git_output(source: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source), *args],
        timeout=60,
    )


def checkout_info(source: Path) -> tuple[dict, dict[str, str]]:
    revision = git_output(source, "rev-parse", "HEAD").decode().strip()
    tree = git_output(source, "rev-parse", "HEAD:data").decode().strip()
    entries = git_output(source, "ls-tree", "-r", "-z", "HEAD", "--", "data/")
    blobs = {}
    for entry in entries.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, digest = metadata.split()
            if kind == b"blob" and mode == b"100644":
                blobs[path.decode().removeprefix("data/")] = digest.decode()
    return {"repository": UPSTREAM, "commit": revision, "data_tree": tree}, blobs


def image_paths(mappings: dict) -> set[str]:
    paths = set()
    for name, fields in IMAGE_FIELDS.items():
        rows = mappings[name]
        if name == "ranks":
            rows = rows["rank"] + rows["leisure_rank"]
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name} 映射为空或格式无效")
        for row in rows:
            if row.get("verified") is not True:
                continue
            for field in fields:
                extracted = row.get("extracted_paths", {}).get(field)
                value = extracted.get("png") if isinstance(extracted, dict) else extracted
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError(f"{name}.{field} 图片路径无效")
                path = PurePosixPath(value)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in value
                    or ":" in value
                    or not value.startswith("images/")
                    or path.suffix != ".png"
                ):
                    raise ValueError(f"资源图片路径越界或无效：{value}")
                paths.add(value)
    return paths


def install_resources(
    source: Path, target: Path, info: dict, blobs: dict[str, str]
) -> tuple[dict, int]:
    source, target = source.resolve(), target.resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("源目录和目标目录不能重叠")
    version = json.loads((source / "version.json").read_text(encoding="utf-8"))
    if not isinstance(version.get("version"), str) or not version["version"]:
        raise ValueError("上游缺少游戏版本")
    mappings = {
        name: json.loads((source / "mappings" / f"{name}.json").read_text(encoding="utf-8"))
        for name in (*IMAGE_FIELDS, "game_modes", "enums")
    }
    if not mappings["game_modes"].get("room_mode_display") or not mappings["enums"]:
        raise ValueError("上游模式或枚举映射为空")
    images = image_paths(mappings)
    files = sorted(
        {"version.json", "build-report.json"}
        | {p.relative_to(source).as_posix() for p in (source / "mappings").glob("*.json")}
        | {p.relative_to(source).as_posix() for p in (source / "tables").glob("*.json")}
        | images
    )
    if not any(path.startswith("tables/") for path in files):
        raise ValueError("上游配置表为空")
    target.parent.mkdir(parents=True, exist_ok=True)
    changed = 0
    content_hash = hashlib.sha256()
    # Validate the entire import before replacing the live directory. Local fonts and
    # Wiki portraits are copied into the candidate alongside the updated game data.
    with tempfile.TemporaryDirectory(prefix=".strinova-update-", dir=target.parent) as temporary:
        candidate = Path(temporary) / "resources"
        if target.exists():
            shutil.copytree(target, candidate)
        else:
            candidate.mkdir()
        for relative in files:
            path = (source / relative).resolve()
            if not path.is_relative_to(source):
                raise ValueError(f"源资源路径越界：{relative}")
            data = path.read_bytes()
            blob_hash = hashlib.sha1(
                f"blob {len(data)}\0".encode() + data, usedforsecurity=False
            ).hexdigest()
            if blobs.get(relative) != blob_hash:
                raise ValueError(f"资源与上游提交不一致：{relative}")
            if relative in images:
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != "PNG":
                        raise ValueError(f"图片不是 PNG：{relative}")
                    image.verify()
            else:
                json.loads(data)
            content_hash.update(relative.encode() + b"\0" + hashlib.sha256(data).digest())
            destination = candidate / relative
            if not destination.is_file() or destination.read_bytes() != data:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                changed += 1
        if changed:
            backup = Path(temporary) / "previous"
            if target.exists():
                target.rename(backup)
            try:
                candidate.rename(target)
            except OSError:
                if backup.exists():
                    backup.rename(target)
                raise
    state = {
        **info,
        "game_version": version["version"],
        "content_sha256": content_hash.hexdigest(),
        "mappings": sum(path.startswith("mappings/") for path in files),
        "tables": sum(path.startswith("tables/") for path in files),
        "images": len(images),
    }
    return state, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 kqhub 本地检出更新战绩资源，不启动任何服务")
    parser.add_argument("--source", type=Path, required=True, help="calabiyau-resource 仓库目录")
    args = parser.parse_args(argv)
    try:
        source = args.source.expanduser().resolve()
        info, blobs = checkout_info(source)
        state, changed = install_resources(source / "data", PLUGIN_DIR / "resources", info, blobs)
        (PLUGIN_DIR / "resource_source.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"资源更新失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"资源已更新至 {state['game_version']} / {state['commit'][:12]}："
        f"{state['mappings']} 份映射、{state['tables']} 张配置表、"
        f"{state['images']} 张图片，变更 {changed} 个文件。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
