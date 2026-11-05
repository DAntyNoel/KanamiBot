"""Compare exported CPU ONNX models with source checkpoints on local example images."""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import snapshot_download
from PIL import Image, ImageOps
from transformers import AutoModelForImageClassification, ViTImageProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kanamibot.plugins.image_classifier.classifier import ImageClassifier  # noqa: E402
from kanamibot.plugins.image_classifier.config import ROOT, Settings  # noqa: E402
from kanamibot.plugins.image_classifier.models import SPECS, file_sha256, preprocess  # noqa: E402
from kanamibot.plugins.image_classifier.storage import ResultStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--source-cache", type=Path, default=ROOT / "export-cache")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "validation")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(model_dir=args.model_dir, anime_real_enabled=False)
    classifier = ImageClassifier(settings)
    store = ResultStore(args.output_dir / "results")
    records = []
    rgb_images = []
    for path in args.images:
        data = path.read_bytes()
        with Image.open(path) as image:
            rgb_images.append(ImageOps.exif_transpose(image).convert("RGB"))
        started = time.perf_counter()
        result = classifier.classify(data)
        elapsed = time.perf_counter() - started
        saved = store.save(
            data,
            result,
            source={"local_file": str(path.resolve())},
            models=classifier.provenance(),
            thresholds={"anime": 0.5, "nsfw": 0.5},
        )
        records.append(
            {
                "image": path.name,
                "input_sha256": file_sha256(path),
                "result_file": str(saved.resolve()),
                "cpu_seconds": elapsed,
                "classification": result.to_dict(),
                "comparison": {},
            }
        )
    torch.set_num_threads(2)
    for spec, exported in zip(SPECS[:2], (classifier.anime, classifier.nsfw), strict=True):
        snapshot = snapshot_download(
            spec.repository,
            revision=spec.revision,
            cache_dir=str(args.source_cache),
            local_files_only=True,
        )
        processor = ViTImageProcessor.from_pretrained(snapshot, local_files_only=True)
        model = (
            AutoModelForImageClassification.from_pretrained(
                snapshot,
                local_files_only=True,
                use_safetensors=True,
                attn_implementation="eager",
            )
            .cpu()
            .eval()
        )
        for image, record in zip(rgb_images, records, strict=True):
            ours = preprocess(image)
            reference = processor(images=image, return_tensors="pt")["pixel_values"]
            np.testing.assert_allclose(ours, reference.numpy(), rtol=0, atol=1e-6)
            with torch.inference_mode():
                expected = model(pixel_values=reference).logits.numpy()
            actual = exported.session.run(["logits"], {"pixel_values": ours})[0]
            np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-4)
            assert int(actual.argmax()) == int(expected.argmax())
            record["comparison"][spec.key] = {
                "preprocessing_max_error": float(np.abs(ours - reference.numpy()).max()),
                "logits_max_error": float(np.abs(actual - expected).max()),
                "same_top_label": True,
            }
        del model
        gc.collect()
    report = args.output_dir / "report.json"
    report.write_text(
        json.dumps(
            {
                "models": classifier.provenance(),
                "examples": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(report.resolve())
    for record in records:
        result = record["classification"]
        print(
            f"{record['image']}: {result['dimension']}/{result['subtype']}/{result['safety']} "
            f"P(anime)={result['anime_probability']:.6f} P(nsfw)={result['nsfw_probability']:.6f} "
            f"{record['cpu_seconds']:.3f}s"
        )


if __name__ == "__main__":
    main()
