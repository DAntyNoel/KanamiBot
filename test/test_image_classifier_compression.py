from __future__ import annotations

import io
import unittest

import numpy as np
from PIL import Image

from kanamibot.plugins.image_classifier.compression import prepare_upload
from kanamibot.plugins.image_classifier.config import Settings


def encode(image: Image.Image, format: str = "PNG", **kwargs) -> bytes:
    output = io.BytesIO()
    image.save(output, format=format, **kwargs)
    return output.getvalue()


class UploadCompressionTest(unittest.TestCase):
    def test_in_limit_original_bytes_are_unchanged(self):
        data = encode(Image.new("RGB", (30, 20), "red"))
        prepared, metadata = prepare_upload(data, Settings())
        self.assertIs(prepared, data)
        self.assertIsNone(metadata)

    def test_pixel_limit_resizes_without_stretching_or_losing_alpha(self):
        data = encode(Image.new("RGBA", (200, 100), (255, 0, 0, 128)))
        prepared, metadata = prepare_upload(data, Settings(max_pixels=5000))
        with Image.open(io.BytesIO(prepared)) as image:
            self.assertEqual(image.size, (100, 50))
            self.assertEqual(image.getpixel((10, 10)), (255, 0, 0, 128))
        self.assertEqual(metadata["original_width"], 200)

    def test_byte_limit_reencodes_large_bmp(self):
        data = encode(Image.new("RGB", (700, 600), "blue"), "BMP")
        self.assertGreater(len(data), 1024 * 1024)
        prepared, metadata = prepare_upload(data, Settings(max_download_mb=1))
        self.assertLessEqual(len(prepared), 1024 * 1024)
        self.assertEqual(metadata["prepared_bytes"], len(prepared))

    def test_noisy_image_uses_lossy_fallback_within_budget(self):
        pixels = np.random.default_rng(0).integers(0, 256, (1000, 1000, 3), dtype=np.uint8)
        prepared, metadata = prepare_upload(
            encode(Image.fromarray(pixels)), Settings(max_download_mb=1)
        )
        self.assertLessEqual(len(prepared), 1024 * 1024)
        self.assertEqual(metadata["format"], "webp")

    def test_animation_keeps_samples_from_beginning_middle_and_end(self):
        frames = [
            Image.new("RGB", (200, 100), color)
            for color in ("red", "green", "blue", "white", "black")
        ]
        data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100)
        prepared, metadata = prepare_upload(data, Settings(max_pixels=5000, animation_samples=3))
        self.assertEqual(metadata["sampled_indices"], [0, 2, 4])
        self.assertEqual(metadata["original_frames"], 5)
        with Image.open(io.BytesIO(prepared)) as image:
            self.assertEqual(image.n_frames, 3)
            self.assertEqual(image.size, (100, 50))
            image.seek(2)
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (0, 0, 0))

    def test_corrupt_oversized_file_cannot_bypass_decoder(self):
        with self.assertRaises(OSError):
            prepare_upload(b"x" * (1024 * 1024 + 1), Settings(max_download_mb=1))
