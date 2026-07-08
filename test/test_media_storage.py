from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kanamibot.core.media_storage import AdvancedMediaStorageSystem


class AdvancedMediaStorageCollisionTest(unittest.TestCase):
    def test_private_storage_verifies_hash_collision_before_deduping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AdvancedMediaStorageSystem(
                "gptimage2",
                data_root=Path(temp_dir) / "codex_gpt" / "images",
                refresh_global_index=False,
            )

            with patch.object(storage, "_calculate_hash_from_bytes", return_value="same-hash"):
                first = storage.upload(
                    b"qq-image-one",
                    ext=".bin",
                    original_name="first.bin",
                    verify_hash_collision=True,
                    media_source="qq",
                )
                duplicate = storage.upload(
                    b"qq-image-one",
                    ext=".bin",
                    original_name="duplicate.bin",
                    verify_hash_collision=True,
                    media_source="qq",
                )
                collision = storage.upload(
                    b"qq-image-two",
                    ext=".bin",
                    original_name="collision.bin",
                    verify_hash_collision=True,
                    media_source="qq",
                )

            self.assertEqual(first["status"], "created")
            self.assertEqual(duplicate["status"], "exists")
            self.assertEqual(duplicate["file_id"], first["file_id"])
            self.assertEqual(collision["status"], "created")
            self.assertNotIn("hash_collision", collision)
            self.assertNotIn("collides_with", collision)
            self.assertEqual(len(storage.metadata_registry), 2)
            self.assertFalse((storage.data_root / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
