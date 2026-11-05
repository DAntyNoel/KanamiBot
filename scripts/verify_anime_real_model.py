"""Check our DeepGHS adapter against the upstream imgutils preprocessing and ONNX output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kanamibot.plugins.image_classifier.classifier import ImageClassifier  # noqa: E402
from kanamibot.plugins.image_classifier.config import ROOT, Settings  # noqa: E402
from kanamibot.plugins.image_classifier.models import SPECS, file_sha256, preprocess  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "validation" / "anime-real-report.json"
    )
    args = parser.parse_args()
    classifier = ImageClassifier(
        Settings(
            model_dir=args.model_dir,
            anime_enabled=False,
            nsfw_enabled=False,
        )
    )
    model = classifier.anime_real
    reports = []
    for path in args.images:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            background.paste(rgba, (0, 0), mask=rgba)
            resized = background.convert("RGB").resize((384, 384), Image.Resampling.BILINEAR)
            # imgutils: rgb_encode -> normalize, matching float32 -> float64 -> float32.
            reference = (np.asarray(resized).transpose(2, 0, 1) / 255.0).astype(np.float32)
            reference = ((reference - np.array([0.5])[:, None, None]) / 0.5).astype(np.float32)
            reference = reference[None]
            ours = preprocess(image, SPECS[2])
            np.testing.assert_array_equal(ours, reference)
            expected = model.session.run(["output"], {"input": reference})[0][0]
        result = classifier.classify(path.read_bytes())
        actual = np.asarray([result.anime_real_scores[label] for label in SPECS[2].labels])
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
        reports.append(
            {
                "image": path.name,
                "sha256": file_sha256(path),
                "scores": result.anime_real_scores,
                "preprocessing_max_error": float(np.abs(ours - reference).max()),
                "probabilities_max_error": float(np.abs(actual - expected).max()),
                "enabled_models": result.enabled_models,
            }
        )
        print(path.name, result.anime_real_scores, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "models": classifier.provenance(),
                "examples": reports,
                "reference": "https://github.com/deepghs/imgutils/blob/main/imgutils/generic/classify.py",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
