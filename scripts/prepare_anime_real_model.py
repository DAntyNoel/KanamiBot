"""Download the pinned official DeepGHS ONNX model; no conversion dependencies needed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kanamibot.plugins.image_classifier.config import ROOT  # noqa: E402
from kanamibot.plugins.image_classifier.models import SPECS, OnnxModel, file_sha256  # noqa: E402

EXPECTED_SHA256 = "c02d68cc7f56dbcbcd4f41a10df190385c1f963f22c4eefb8d162869800ed3b0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    spec = next(spec for spec in SPECS if spec.key == "anime_real")
    bundle = args.output_dir / spec.key
    bundle.mkdir(parents=True, exist_ok=True)
    base = f"https://huggingface.co/{spec.repository}/resolve/{spec.revision}/{spec.variant}"
    with urllib.request.urlopen(f"{base}/meta.json", timeout=60) as response:
        meta = json.load(response)
    if meta.get("labels") != list(spec.labels):
        raise ValueError("Unexpected upstream label order")
    target = bundle / "model.onnx"
    if not target.exists() or file_sha256(target) != EXPECTED_SHA256:
        pending = bundle / f"model.{uuid.uuid4().hex}.pending"
        try:
            with urllib.request.urlopen(f"{base}/model.onnx", timeout=60) as response:
                with pending.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            if file_sha256(pending) != EXPECTED_SHA256:
                raise ValueError("Official ONNX checksum mismatch")
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
    manifest = {
        "repository": spec.repository,
        "revision": spec.revision,
        "variant": spec.variant,
        "labels": list(spec.labels),
        "preprocessing": spec.preprocessing,
        "sha256": EXPECTED_SHA256,
        "format": "official-onnx",
        "output": "probabilities",
    }
    (bundle / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    pending_manifest = bundle / "manifest.pending.json"
    pending_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(pending_manifest, bundle / "manifest.json")
    OnnxModel(args.output_dir, spec)
    print(f"Verified official CPU ONNX: {target.resolve()}")


if __name__ == "__main__":
    main()
