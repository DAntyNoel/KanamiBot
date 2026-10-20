from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

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
        row = {"id": 1, "verified": True, "extracted_paths": {}}
        for name, fields in update.IMAGE_FIELDS.items():
            value = {**row, "extracted_paths": {fields[0]: {"png": "images/icon.png"}}}
            self.mappings[name] = (
                {"rank": [value], "leisure_rank": [value]} if name == "ranks" else [value]
            )
        self.mappings["game_modes"] = {"room_mode_display": {"1": {"verified": True}}}
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


if __name__ == "__main__":
    unittest.main()
