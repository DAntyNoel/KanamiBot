"""Offline CPU benchmark; does not start NoneBot or connect to QQ."""
from __future__ import annotations

import io
import json
import platform
import statistics
import time

from PIL import Image

from kanamibot.plugins.image_classifier.classifier import ImageClassifier
from kanamibot.plugins.image_classifier.config import Settings


def main() -> None:
    settings = Settings()
    started = time.perf_counter()
    classifier = ImageClassifier(settings)
    output = {"cpu": platform.processor(), "threads": settings.cpu_threads,
              "load_seconds": round(time.perf_counter() - started, 3), "samples": {}}
    buffer = io.BytesIO()
    Image.effect_noise((1024, 1024), 64).convert("RGB").save(buffer, format="JPEG")
    data = buffer.getvalue()
    for keys in [(key,) for key in classifier.available_models()] + [classifier.available_models()]:
        classifier.classify(data, keys)
        timings = []
        for _ in range(10):
            started = time.perf_counter()
            classifier.classify(data, keys)
            timings.append((time.perf_counter() - started) * 1000)
        output["samples"]["+".join(keys)] = {
            "median_ms": round(statistics.median(timings), 2),
            "max_ms": round(max(timings), 2),
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
