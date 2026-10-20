from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PIL import Image

from kanamibot.strinova.query import Query
from kanamibot.strinova.render import render_html
from kanamibot.strinova.resources import ResourcePack
from kanamibot.strinova.service import Report

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_strinova_resources.py"
SPEC = importlib.util.spec_from_file_location("update_strinova_resources", SCRIPT)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


class ResourceUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "resources"
        self.mappings = {}
        row = {"id": 1, "verified": True, "name": "旧地图", "name_cn": "旧角色"}
        for name, fields in update.IMAGE_FIELDS.items():
            value = {
                **row,
                "extracted_paths": {field: {"png": "images/icon.png"} for field in fields},
            }
            self.mappings[name] = (
                {"rank": [value], "leisure_rank": [value]} if name == "ranks" else [value]
            )
        self.mappings["game_modes"] = {
            "room_mode_display": {"1": {"verified": True, "name_cn": "旧模式"}}
        }
        self.mappings["enums"] = {"ncmd_cs.EOnlineStatus": {"OnlineStatus_ONLINE": 1}}
        for name, value in self.mappings.items():
            self.write_json(f"mappings/{name}.json", value)
        self.write_json("version.json", {"version": "1.18.5.10"})
        self.write_json("build-report.json", {"images": 1})
        self.write_json("tables/Role.json", [{"id": 1}])
        with io.BytesIO() as buffer:
            Image.new("RGBA", (8, 8), "red").save(buffer, format="PNG")
            self.write("images/icon.png", buffer.getvalue())
        self.blobs = self.blob_hashes()
        self.info = {"repository": update.UPSTREAM, "commit": "a" * 40, "data_tree": "b" * 40}

    def write(self, relative: str, data: bytes):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def write_json(self, relative: str, value):
        self.write(relative, json.dumps(value).encode())

    def blob_hashes(self):
        hashes = {}
        for path in self.source.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                hashes[path.relative_to(self.source).as_posix()] = hashlib.sha1(
                    f"blob {len(data)}\0".encode() + data, usedforsecurity=False
                ).hexdigest()
        return hashes

    def install(self):
        return update.install_resources(self.source, self.target, self.info, self.blobs)

    def prepare_new_version(self):
        self.mappings["maps"][0]["name"] = "新地图"
        self.mappings["characters"][0]["name_cn"] = "新角色"
        self.mappings["avatars"].append({**self.mappings["avatars"][0], "id": 2})
        self.mappings["game_modes"]["room_mode_display"]["1"]["name_cn"] = "新模式"
        self.mappings["enums"]["ncmd_cs.EOnlineStatus"]["OnlineStatus_ONLINE"] = 2
        for name, value in self.mappings.items():
            self.write_json(f"mappings/{name}.json", value)
        with io.BytesIO() as buffer:
            Image.new("RGBA", (8, 8), "blue").save(buffer, format="PNG")
            self.write("images/icon.png", buffer.getvalue())
        self.blobs = self.blob_hashes()

    def test_updates_resources_preserves_local_assets_and_repeats_without_changes(self):
        for relative in ("fonts/local.ttf", "images/wiki/portraits/101.png"):
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"local asset")
        state, changed = self.install()
        self.assertEqual((state["images"], state["mappings"], state["tables"]), (1, 6, 1))
        self.assertGreater(changed, 0)
        self.assertEqual(state["game_version"], "1.18.5.10")
        self.assertEqual((self.target / "fonts/local.ttf").read_bytes(), b"local asset")
        self.assertEqual(
            (self.target / "images/wiki/portraits/101.png").read_bytes(), b"local asset"
        )
        self.assertEqual(self.install(), (state, 0))

    def test_modified_or_missing_source_does_not_replace_existing_resources(self):
        self.install()
        original = (self.target / "images/icon.png").read_bytes()
        self.write("images/icon.png", b"changed")
        with self.assertRaisesRegex(ValueError, "与上游提交不一致"):
            self.install()
        self.assertEqual((self.target / "images/icon.png").read_bytes(), original)
        (self.source / "images/icon.png").unlink()
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual((self.target / "images/icon.png").read_bytes(), original)

    def test_invalid_upstream_png_does_not_replace_existing_resources(self):
        self.install()
        original = (self.target / "images/icon.png").read_bytes()
        self.write("images/icon.png", b"not a PNG")
        self.blobs = self.blob_hashes()
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual((self.target / "images/icon.png").read_bytes(), original)

    def test_rejects_image_paths_outside_images_directory(self):
        for value in ("../outside.png", "images/../outside.png", "/images/a.png", "C:/a.png"):
            with self.subTest(value=value):
                self.mappings["avatars"][0]["extracted_paths"]["IconItem"]["png"] = value
                with self.assertRaisesRegex(ValueError, "路径越界"):
                    update.image_paths(self.mappings)

    def test_rejects_overlapping_source_and_target(self):
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            update.install_resources(self.source, self.source / "nested", self.info, self.blobs)

    def test_same_pack_and_renderer_use_new_metadata_images_and_badges_after_install(self):
        self.install()
        pack = ResourcePack(self.target)
        image = pack.data_uri(pack.avatar_path(1))
        ranked = pack.rank_image(1)
        leisure = pack.rank_image(1, leisure=True)
        self.assertEqual(pack.map_name(1), "旧地图")
        self.assertEqual(pack.mode_name(1), "旧模式")
        self.assertEqual(pack.status_name(1), "在线")
        report = Report(
            Query("测试玩家"),
            "123456",
            {"nick": "测试玩家", "freq_roles": [1]},
            sections={"standings": {"titles": [{"map_id": 1, "role_id": 1}]}},
        )
        with patch("kanamibot.strinova.render.resource_pack", return_value=pack):
            before = render_html(report)
            self.prepare_new_version()
            self.install()
            after = render_html(report)
        self.assertIn("旧地图", before)
        self.assertIn("旧角色", before)
        self.assertIn("新地图", after)
        self.assertIn("新角色", after)
        self.assertNotIn("旧地图", after)
        self.assertEqual(pack.mode_name(1), "新模式")
        self.assertEqual(pack.status_name(2), "在线")
        self.assertEqual(pack.status_name(1), "状态码 1")
        self.assertIsNotNone(pack.avatar_path(2))
        self.assertNotEqual(pack.data_uri(pack.avatar_path(1)), image)
        self.assertNotEqual(pack.role_image(1), image)
        self.assertNotEqual(pack.rank_image(1), ranked)
        self.assertNotEqual(pack.rank_image(1, leisure=True), leisure)

    def test_same_timestamp_directory_swap_refreshes_fonts_and_wiki_portraits(self):
        self.install()
        font = self.target / "fonts/local.ttf"
        font.parent.mkdir()
        font.write_bytes(b"old font")
        portrait = self.target / "images/wiki/portraits/1.png"
        portrait.parent.mkdir(parents=True)
        Image.new("RGBA", (8, 8), "pink").save(portrait)
        pack = ResourcePack(self.target)
        old_font, old_portrait = pack.data_uri(font), pack.role_image(1, portrait=True)
        candidate, backup = self.root / "candidate", self.root / "previous"
        shutil.copytree(self.target, candidate)
        (candidate / "fonts/local.ttf").write_bytes(b"new font")
        Image.new("RGBA", (8, 8), "green").save(candidate / "images/wiki/portraits/1.png")
        stat = self.target.stat()
        os.utime(candidate, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.target.rename(backup)
        candidate.rename(self.target)
        self.assertNotEqual(pack.data_uri(font), old_font)
        self.assertNotEqual(pack.role_image(1, portrait=True), old_portrait)

    def test_directory_swap_gap_keeps_cached_values_and_does_not_cache_missing_mappings(self):
        self.install()
        (self.target / "mappings/channels.json").write_text('{"ready": true}')
        pack = ResourcePack(self.target)
        self.assertEqual(pack.map_name(1), "旧地图")
        image_path = pack.avatar_path(1)
        image, badge = pack.data_uri(image_path), pack.rank_image(1)
        backup = self.root / "previous"
        self.target.rename(backup)
        self.assertEqual(pack.map_name(1), "旧地图")
        self.assertEqual(pack.data_uri(image_path), image)
        self.assertEqual(pack.rank_image(1), badge)
        self.assertEqual(pack.mapping("channels"), {})
        backup.rename(self.target)
        self.assertEqual(pack.mapping("channels"), {"ready": True})

    def test_failed_promotion_rolls_back_without_invalidating_live_pack(self):
        self.install()
        pack = ResourcePack(self.target)
        image = pack.role_image(1)
        self.assertEqual(pack.map_name(1), "旧地图")
        self.prepare_new_version()
        rename = Path.rename

        def fail_promotion(path, destination):
            if path.name == "resources" and path.parent != self.root:
                raise OSError("promotion failed")
            return rename(path, destination)

        with patch.object(Path, "rename", fail_promotion):
            with self.assertRaisesRegex(OSError, "promotion failed"):
                self.install()
        self.assertEqual(pack.map_name(1), "旧地图")
        self.assertEqual(pack.role_image(1), image)

    def test_in_flight_old_image_cannot_repopulate_refreshed_cache(self):
        self.install()
        pack = ResourcePack(self.target)
        path = self.target / "images/icon.png"
        old = ResourcePack(self.target).data_uri(path)
        reading, resume = Event(), Event()
        read_bytes = Path.read_bytes

        def delayed_read(current):
            data = read_bytes(current)
            if current == path and not reading.is_set():
                reading.set()
                if not resume.wait(5):
                    raise TimeoutError("reader did not resume")
            return data

        with (
            ThreadPoolExecutor(max_workers=2) as executor,
            patch.object(Path, "read_bytes", delayed_read),
        ):
            first = executor.submit(pack.data_uri, path)
            try:
                self.assertTrue(reading.wait(5))
                self.prepare_new_version()
                self.install()
                second = executor.submit(pack.data_uri, path)
                try:
                    second.result(timeout=0.1)
                except TimeoutError:
                    pass
            finally:
                resume.set()
            self.assertEqual(first.result(timeout=5), old)
            new = second.result(timeout=5)
        self.assertNotEqual(new, old)
        self.assertEqual(pack.data_uri(path), new)


if __name__ == "__main__":
    unittest.main()
