"""Bounded, in-memory preparation for oversized uploads in the local web lab."""

from __future__ import annotations

import io
import math
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings


def prepare_upload(data: bytes, settings: Settings) -> tuple[bytes, dict | None]:
    max_bytes = settings.max_download_mb * 1024 * 1024
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data))
        except UnidentifiedImageError:
            # Let the classifier report invalid files as usual when no resizing is needed.
            if len(data) <= max_bytes:
                return data, None
            raise
        with image:
            width, height = image.size
            if len(data) <= max_bytes and width * height <= settings.max_pixels:
                return data, None
            total_frames = getattr(image, "n_frames", 1)
            count = min(total_frames, settings.animation_samples)
            indices = (
                [round(i * (total_frames - 1) / (count - 1)) for i in range(count)]
                if count > 1
                else [0]
            )
            scale = min(
                1,
                2048 / max(width, height),
                math.sqrt(
                    settings.max_pixels / (width * height),
                ),
            )
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            frames = []
            for index in indices:
                image.seek(index)
                frame = ImageOps.exif_transpose(image)
                frame.thumbnail(size, Image.Resampling.LANCZOS)
                frames.append(frame.convert("RGBA"))

    # PNG preserves transparency and exact pixels if it already fits. For animation,
    # lossless WebP keeps the sampled frames rather than silently using the first.
    animated = len(frames) > 1
    output_format = "WEBP" if animated else "PNG"
    for quality in (None, 90, 80, 70):
        output = io.BytesIO()
        options = (
            {"save_all": True, "append_images": frames[1:], "duration": 100, "loop": 0}
            if animated
            else {}
        )
        if quality is not None:
            output_format = "WEBP"
            options.update(quality=quality, method=4)
        elif animated:
            options.update(lossless=True, method=4)
        frames[0].save(output, format=output_format, **options)
        if output.tell() <= max_bytes:
            break
    else:
        # Very noisy images may still exceed a small configured byte budget.
        while output.tell() > max_bytes:
            if max(frames[0].size) == 1:
                raise ValueError("Cannot compress image within byte limit")
            for frame in frames:
                frame.thumbnail(
                    (max(1, frame.width * 3 // 4), max(1, frame.height * 3 // 4)),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            frames[0].save(output, format="WEBP", **options)
    prepared = output.getvalue()
    return prepared, {
        "original_bytes": len(data),
        "prepared_bytes": len(prepared),
        "original_width": width,
        "original_height": height,
        "width": frames[0].width,
        "height": frames[0].height,
        "original_frames": total_frames,
        "prepared_frames": len(frames),
        "sampled_indices": indices,
        "format": output_format.lower(),
    }
