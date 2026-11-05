from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repository: str
    revision: str
    labels: tuple[str, ...]
    preprocessing: str = "vit-224-bilinear-rescale-normalize-0.5"
    size: int = 224
    input_name: str = "pixel_values"
    output_name: str = "logits"
    probabilities: bool = False
    variant: str | None = None


SPECS = (
    ModelSpec(
        "anime",
        "antonioglass/real-or-anime-age",
        "bd208a02c3d0a15de26d1c94ce4624b017ea3639",
        ("anime_adult", "anime_contentious", "anime_non_adult", "non_anime"),
    ),
    ModelSpec(
        "nsfw",
        "Falconsai/nsfw_image_detection",
        "96cb0d0342c7afb80cab76ecc58b265fa44da256",
        ("normal", "nsfw"),
    ),
    ModelSpec(
        "anime_real",
        "deepghs/anime_real_cls",
        "097a6c1e9e62866a435810c883a45a7cb2c7077d",
        ("anime", "real"),
        preprocessing="imgutils-384-bilinear-white-normalize-0.5",
        size=384,
        input_name="input",
        output_name="output",
        probabilities=True,
        variant="mobilenetv3_v1.4_dist",
    ),
)
MODEL_KEYS = tuple(spec.key for spec in SPECS)


def file_sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def preprocess(image: Image.Image, spec: ModelSpec | None = None) -> np.ndarray:
    """Match the pinned model's upstream RGB/resize/normalization pipeline."""
    size = spec.size if spec else 224
    if spec and spec.key == "anime_real":
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.paste(rgba, (0, 0), mask=rgba)
        image = background.convert("RGB")
    resized = image.convert("RGB").resize((size, size), Image.Resampling.BILINEAR)
    pixels = (
        (np.asarray(resized) / 255.0).astype(np.float32)
        if spec and spec.key == "anime_real"
        else np.asarray(resized).astype(np.float32) * np.float32(1 / 255)
    )
    pixels = (pixels - np.float32(0.5)) / np.float32(0.5)
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])


class OnnxModel:
    def __init__(self, directory: Path, spec: ModelSpec, cpu_threads: int = 2) -> None:
        self.spec = spec
        bundle = directory / spec.key
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("repository") != spec.repository
            or manifest.get("revision") != spec.revision
            or manifest.get("labels") != list(spec.labels)
            or manifest.get("preprocessing") != spec.preprocessing
            or (spec.variant is not None and manifest.get("variant") != spec.variant)
        ):
            raise ValueError(f"Invalid model manifest: {bundle}")
        model = bundle / "model.onnx"
        self.sha256 = file_sha256(model)
        if self.sha256 != manifest.get("sha256"):
            raise ValueError(f"ONNX checksum mismatch: {model}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = cpu_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].name != spec.input_name
            or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 4
            or inputs[0].shape[1:] != [3, spec.size, spec.size]
            or (isinstance(inputs[0].shape[0], int) and inputs[0].shape[0] != 1)
            or len(outputs) != 1
            or outputs[0].name != spec.output_name
            or len(outputs[0].shape) != 2
            or outputs[0].shape[1] != len(spec.labels)
            or outputs[0].type != "tensor(float)"
        ):
            raise ValueError(f"Unexpected ONNX input/output: {model}")

    def predict(self, pixels: np.ndarray) -> dict[str, float]:
        logits = np.asarray(
            self.session.run([self.spec.output_name], {self.spec.input_name: pixels})[0][0]
        )
        if not np.isfinite(logits).all():
            raise ValueError(f"Non-finite output from {self.spec.repository}")
        if self.spec.probabilities:
            # The official DeepGHS ONNX graph already includes Softmax.
            if (logits < 0).any() or (logits > 1).any() or not np.isclose(logits.sum(), 1):
                raise ValueError(f"Invalid probabilities from {self.spec.repository}")
            probabilities = logits
        else:
            weights = np.exp(logits.astype(np.float64) - float(logits.max()))
            probabilities = weights / weights.sum()
        return dict(zip(self.spec.labels, map(float, probabilities), strict=True))
