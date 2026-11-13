from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from kanamibot.strinova import resource_auto_update as auto
from kanamibot.strinova.resource_import import IMAGE_FIELDS, UPSTREAM
from kanamibot.strinova.resources import ResourcePack

COMMIT = "a" * 40
TAG_COMMIT = "b" * 40
REFS = f"{TAG_COMMIT}\trefs/tags/v0.6.4"


class AutoResourceUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state.json"
        self.target = self.root / "resources"
        self.target.mkdir()
        self.font = self.target / "fonts/local.ttf"
        self.font.parent.mkdir()
        self.font.write_bytes(b"local font")
        self.calls = []
        self.blobs = {}
        self.bad_image = False
        self.info = {"repository": UPSTREAM, "commit": COMMIT, "data_tree": "c" * 40}
        git_patch = patch.object(auto, "git", side_effect=self.git)
        info_patch = patch.object(
            auto, "checkout_info", side_effect=lambda _: (self.info, self.blobs)
        )
        git_patch.start()
        info_patch.start()
        self.addCleanup(git_patch.stop)
        self.addCleanup(info_patch.stop)

    def write_source(self, source):
        data = source / "data"
        documents = {
            "version.json": {"version": "1.18.7.17"},
            "build-report.json": {},
            "tables/Role.json": [{"id": 137}],
            "mappings/enums.json": {"ready": True},
            "mappings/game_modes.json": {"room_mode_display": {"1": {"name_cn": "爆破"}}},
        }
        for name, fields in IMAGE_FIELDS.items():
            row = {
                "id": 137, "verified": True, "name_cn": "香奈美", "name": "测试地图",
                "extracted_paths": {key: {"png": "images/icon.png"} for key in fields},
            }
            documents[f"mappings/{name}.json"] = (
                {"rank": [row], "leisure_rank": [row]} if name == "ranks" else [row]
            )
        files = {name: json.dumps(value).encode() for name, value in documents.items()}
        buffer = io.BytesIO()
        Image.new("RGBA", (8, 8), "red").save(buffer, format="PNG")
        files["images/icon.png"] = b"invalid PNG" if self.bad_image else buffer.getvalue()
        for relative, content in files.items():
            path = data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.blobs[relative] = hashlib.sha1(
                f"blob {len(content)}\0".encode() + content, usedforsecurity=False
            ).hexdigest()

    def git(self, *args, source=None, input_text=None):
        self.calls.append((args, source))
        if args == ("ls-remote", "--tags", auto.HTTP_UPSTREAM):
            return REFS
        if args == ("ls-remote", UPSTREAM + ".git", "HEAD"):
            return f"{COMMIT}\tHEAD"
        if "checkout" in args:
            self.write_source(source)
        return ""

    def check(self):
        return auto.check_resource_update(state_path=self.state, target=self.target)

    def test_first_sync_preserves_local_assets_hot_reloads_and_skips_same_refs(self):
        pack = ResourcePack(self.target)
        self.assertEqual(pack.records("characters"), {})
        state = self.check()
        self.assertEqual(state["tag"], "v0.6.4")
        self.assertGreater(state["changed_files"], 0)
        self.assertEqual(pack.records("characters")[137]["name_cn"], "香奈美")
        self.assertIsNotNone(pack.role_image(137))
        self.assertEqual(self.font.read_bytes(), b"local font")
        fetch = next(args for args, _ in self.calls if args[0] == "fetch")
        self.assertEqual(fetch[-1], COMMIT)
        self.assertIn("--filter=blob:none", fetch)
        self.assertFalse(list(self.root.glob("resource-fetch-*")))
        revision = pack._directory_revision()
        self.calls.clear()
        self.assertIsNone(self.check())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(pack._directory_revision(), revision)

    def test_resource_commit_change_updates_even_without_new_http_tag(self):
        self.check()
        state = json.loads(self.state.read_text())
        state["commit"] = "d" * 40
        self.state.write_text(json.dumps(state))
        self.assertEqual(self.check()["commit"], COMMIT)

    def test_new_tag_triggers_sync_even_with_same_resource_commit(self):
        self.check()
        state = json.loads(self.state.read_text())
        state["tag"] = "v0.6.3"
        self.state.write_text(json.dumps(state))
        self.assertEqual(self.check()["tag"], "v0.6.4")

    def test_invalid_png_keeps_old_state_assets_and_retries(self):
        self.check()
        saved = json.loads(self.state.read_text())
        saved["commit"] = "d" * 40
        self.state.write_text(json.dumps(saved))
        previous = self.state.read_bytes()
        original = (self.target / "images/icon.png").read_bytes()
        self.bad_image = True
        with self.assertRaises(OSError):
            self.check()
        self.assertEqual(self.state.read_bytes(), previous)
        self.assertEqual((self.target / "images/icon.png").read_bytes(), original)
        self.assertFalse(list(self.root.glob("resource-fetch-*")))
        self.bad_image = False
        self.assertEqual(self.check()["commit"], COMMIT)

    def test_auth_failure_and_wrong_checkout_do_not_advance_state(self):
        with patch.object(auto, "git", side_effect=RuntimeError("凭据不可用")):
            with self.assertRaises(RuntimeError):
                self.check()
        self.assertFalse(self.state.exists())
        self.info["commit"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "检出提交"):
            self.check()
        self.assertFalse(self.state.exists())
        self.assertEqual(self.font.read_bytes(), b"local font")

    def test_corrupt_state_does_not_overwrite_pending_version(self):
        self.state.write_text("[]")
        with self.assertRaisesRegex(ValueError, "状态无效"):
            self.check()
        self.assertEqual(self.state.read_text(), "[]")


class TagAndGitTests(unittest.TestCase):
    def test_numeric_tag_order_prereleases_and_annotated_tags(self):
        refs = "\n".join([
            f"{'a' * 40}\trefs/tags/v0.9.9",
            f"{'b' * 40}\trefs/tags/v0.10.0^{{}}",
            f"{'c' * 40}\trefs/tags/v0.10.0",
            f"{'d' * 40}\trefs/tags/v0.11.0-rc1",
        ])
        self.assertEqual(auto.latest_tag(refs), {"tag": "v0.10.0", "tag_commit": "b" * 40})
        with self.assertRaises(ValueError):
            auto.latest_tag("no refs")

    def test_git_is_noninteractive_bounded_and_redacts_diagnostics(self):
        with patch.object(auto.subprocess, "run") as run:
            run.return_value.stdout = "ok\n"
            self.assertEqual(auto.git("ls-remote", auto.HTTP_UPSTREAM), "ok")
            self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(run.call_args.kwargs["env"]["GCM_INTERACTIVE"], "Never")
            self.assertEqual(run.call_args.kwargs["timeout"], 600)
            run.side_effect = subprocess.CalledProcessError(1, "git", stderr="secret-token")
            with self.assertRaises(RuntimeError) as error:
                auto.git("ls-remote", auto.HTTP_UPSTREAM)
            self.assertNotIn("secret-token", str(error.exception))
