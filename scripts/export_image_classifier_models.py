"""One-time export on a preparation machine; never needed by the bot runtime."""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from huggingface_hub import snapshot_download
from transformers import AutoModelForImageClassification

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kanamibot.plugins.image_classifier.config import ROOT  # noqa: E402
from kanamibot.plugins.image_classifier.models import SPECS, file_sha256  # noqa: E402


class LogitsOnly(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        return self.model(pixel_values=pixel_values).logits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--download-cache", type=Path, default=ROOT / "export-cache")
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(0)

    for spec in SPECS[:2]:
        print(f"Downloading pinned source: {spec.repository}@{spec.revision}", flush=True)
        snapshot = Path(
            snapshot_download(
                spec.repository,
                revision=spec.revision,
                cache_dir=str(args.download_cache),
                allow_patterns=["config.json", "preprocessor_config.json", "model.safetensors"],
            )
        )
        config = json.loads((snapshot / "config.json").read_text())
        labels = [config["id2label"][str(i)] for i in range(len(spec.labels))]
        processor = json.loads((snapshot / "preprocessor_config.json").read_text())
        expected = {
            "do_normalize": True,
            "do_rescale": True,
            "do_resize": True,
            "image_mean": [0.5] * 3,
            "image_std": [0.5] * 3,
            "resample": 2,
            "size": {"height": 224, "width": 224},
            "rescale_factor": 1 / 255,
        }
        if labels != list(spec.labels) or any(processor.get(k) != v for k, v in expected.items()):
            raise ValueError(f"Unexpected source configuration: {spec.repository}")
        model = (
            LogitsOnly(
                AutoModelForImageClassification.from_pretrained(
                    snapshot,
                    local_files_only=True,
                    use_safetensors=True,
                    attn_implementation="eager",
                )
            )
            .cpu()
            .eval()
        )
        bundle = args.output_dir / spec.key
        bundle.mkdir(parents=True, exist_ok=True)
        pending = bundle / "model.pending.onnx"
        pixels = torch.rand(1, 3, 224, 224) * 2 - 1
        print(f"Exporting {spec.key} to CPU float32 ONNX (opset 17)", flush=True)
        with torch.inference_mode():
            expected_logits = model(pixels).numpy()
            torch.onnx.export(
                model,
                (pixels,),
                str(pending),
                input_names=["pixel_values"],
                output_names=["logits"],
                opset_version=17,
                dynamo=False,
                external_data=False,
            )
        onnx.checker.check_model(str(pending))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        session = ort.InferenceSession(
            str(pending), sess_options=options, providers=["CPUExecutionProvider"]
        )
        actual = session.run(["logits"], {"pixel_values": pixels.numpy()})[0]
        np.testing.assert_allclose(actual, expected_logits, rtol=1e-4, atol=1e-4)
        max_error = float(np.abs(actual - expected_logits).max())
        manifest = {
            "repository": spec.repository,
            "revision": spec.revision,
            "labels": labels,
            "preprocessing": "vit-224-bilinear-rescale-normalize-0.5",
            "sha256": file_sha256(pending),
            "source_sha256": file_sha256(snapshot / "model.safetensors"),
            "opset": 17,
            "dtype": "float32",
            "validation_max_logit_error": max_error,
            "export_versions": {
                name: importlib.metadata.version(name)
                for name in ("torch", "transformers", "onnx", "onnxruntime")
            },
        }
        manifest_pending = bundle / "manifest.pending.json"
        manifest_pending.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(pending, bundle / "model.onnx")
        os.replace(manifest_pending, bundle / "manifest.json")
        print(f"Verified {spec.key}: max logit error={max_error:.8g}", flush=True)
        del model, session
        gc.collect()


if __name__ == "__main__":
    main()
