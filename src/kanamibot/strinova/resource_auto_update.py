"""Poll private upstream refs and merge verified game assets off the event loop."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from kanamibot.core.paths import DATA_DIR

from .resource_import import IMAGE_FIELDS, UPSTREAM, checkout_info, image_paths, install_resources
from .resources import RESOURCE_DIR

HTTP_UPSTREAM = "https://github.com/kqhub-win/strinova-in-game-http.git"
STATE_PATH = DATA_DIR / "strinova" / "resource_auto_update.json"
TAG_PATTERN = re.compile(r"refs/tags/(v(\d+)\.(\d+)\.(\d+))(\^\{\})?")
HASH_PATTERN = re.compile(r"[0-9a-f]{40}")


def git(*args: str, source: Path | None = None, input_text: str | None = None) -> str:
    command = ["git", "-c", "credential.interactive=false"]
    if source is not None:
        command += ["-c", f"safe.directory={source.as_posix()}", "-C", str(source)]
    try:
        result = subprocess.run(
            [*command, *args],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
            capture_output=True,
            text=True,
            input=input_text,
            timeout=600,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        # Git diagnostics can contain credential-helper output; keep logs generic.
        raise RuntimeError("上游 Git 操作失败，请检查网络及运行账号的私有仓库凭据") from None
    return result.stdout.strip()


def latest_tag(refs: str) -> dict[str, str]:
    tags = {}
    for line in refs.splitlines():
        parts = line.split()
        if len(parts) != 2 or not HASH_PATTERN.fullmatch(parts[0]):
            continue
        match = TAG_PATTERN.fullmatch(parts[1])
        if match is not None:
            version = tuple(int(match[i]) for i in (2, 3, 4))
            # Annotated tags resolve to their peeled commit, independent of line order.
            if version not in tags or match[5]:
                tags[version] = {"tag": match[1], "tag_commit": parts[0]}
    if not tags:
        raise ValueError("HTTP 上游未返回有效的正式版 tag")
    return tags[max(tags)]


def read_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(state, dict):
        raise ValueError("资源自动更新状态无效")
    return state


def check_resource_update(
    *, state_path: Path = STATE_PATH, target: Path = RESOURCE_DIR
) -> dict | None:
    """Return applied state, or None when unchanged. Failures leave refs pending."""
    previous = read_state(state_path)
    release = latest_tag(git("ls-remote", "--tags", HTTP_UPSTREAM))
    head = git("ls-remote", UPSTREAM + ".git", "HEAD").split()
    if len(head) != 2 or head[1] != "HEAD" or not HASH_PATTERN.fullmatch(head[0]):
        raise ValueError("资源上游未返回有效的 HEAD")
    commit = head[0]
    if (
        all(previous.get(key) == value for key, value in release.items())
        and previous.get("commit") == commit
        and target.is_dir()
    ):
        return None

    state_path.parent.mkdir(parents=True, exist_ok=True)
    # A fresh checkout avoids changing any user's existing upstream worktree.
    with tempfile.TemporaryDirectory(prefix="resource-fetch-", dir=state_path.parent) as temp:
        source = Path(temp)
        git("init", source=source)
        git("remote", "add", "origin", UPSTREAM + ".git", source=source)
        git("config", "remote.origin.promisor", "true", source=source)
        git("config", "remote.origin.partialclonefilter", "blob:none", source=source)
        git("fetch", "--filter=blob:none", "--depth=1", "origin", commit, source=source)
        patterns = [
            "/data/mappings/", "/data/tables/", "/data/version.json", "/data/build-report.json"
        ]
        git(
            "sparse-checkout", "set", "--no-cone", "--stdin", source=source,
            input_text="\n".join(patterns) + "\n",
        )
        git("-c", "core.hooksPath=", "checkout", "--detach", "FETCH_HEAD", source=source)
        mappings = {
            name: json.loads((source / "data/mappings" / f"{name}.json").read_text("utf-8"))
            for name in IMAGE_FIELDS
        }
        patterns += ["/data/" + path for path in sorted(image_paths(mappings))]
        git(
            "sparse-checkout", "set", "--no-cone", "--stdin", source=source,
            input_text="\n".join(patterns) + "\n",
        )
        info, blobs = checkout_info(source)
        if info["commit"] != commit:
            raise ValueError("资源检出提交与上游检查结果不一致")
        state, changed = install_resources(source / "data", target, info, blobs)
    state.update(release)
    # Runtime state is intentionally separate from the tracked manual-import manifest.
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(state_path)
    return {**state, "changed_files": changed}
