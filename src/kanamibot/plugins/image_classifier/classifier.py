from __future__ import annotations

import gc
import io
import warnings
from dataclasses import asdict, dataclass, field

import numpy as np
from PIL import Image, ImageOps

from .config import Settings
from .models import SPECS, OnnxModel, preprocess


@dataclass(frozen=True)
class Classification:
    dimension: str
    subtype: str
    safety: str
    anime_probability: float | None
    nsfw_probability: float | None
    original_label: str | None
    anime_scores: dict[str, float]
    nsfw_scores: dict[str, float]
    frames: list[dict]
    total_frames: int
    image_format: str
    width: int
    height: int
    anime_real_scores: dict[str, float] = field(default_factory=dict)
    enabled_models: tuple[str, ...] = ()
    dimension_model: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(
    anime_scores: dict[str, float],
    nsfw_scores: dict[str, float],
    settings: Settings,
    anime_real_scores: dict[str, float] | None = None,
) -> dict:
    anime_probability = (
        sum(value for key, value in anime_scores.items() if key != "non_anime")
        if anime_scores
        else (anime_real_scores or {}).get("anime")
    )
    dimension = (
        "unknown"
        if anime_probability is None
        else "anime"
        if anime_probability >= settings.anime_threshold
        else "real"
    )
    subtype = (
        max((key for key in anime_scores if key != "non_anime"), key=anime_scores.__getitem__)
        if dimension == "anime" and anime_scores
        else "non_anime"
        if dimension == "real"
        else "unspecified"
    )
    return {
        "dimension": dimension,
        "subtype": subtype,
        "safety": (
            "unknown"
            if not nsfw_scores
            else "nsfw"
            if nsfw_scores["nsfw"] >= settings.nsfw_threshold
            else "normal"
        ),
        "anime_probability": anime_probability,
        "nsfw_probability": nsfw_scores.get("nsfw"),
        "original_label": max(anime_scores, key=anime_scores.__getitem__) if anime_scores else None,
    }


class ImageClassifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        for spec in SPECS:
            setattr(
                self,
                spec.key,
                (
                    OnnxModel(settings.model_dir, spec, settings.cpu_threads)
                    if getattr(settings, f"{spec.key}_enabled")
                    else None
                ),
            )
        if not self.available_models():
            raise ValueError("No models enabled")

    def available_models(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in SPECS if getattr(self, spec.key) is not None)

    def load_model(self, key: str) -> None:
        """Restore an unloaded session. Callers must serialize against inference."""
        spec = next((spec for spec in SPECS if spec.key == key), None)
        if spec is None:
            raise ValueError(f"Unknown model: {key}")
        if not getattr(self.settings, f"{key}_enabled"):
            raise ValueError(f"Model disabled by configuration: {key}")
        if getattr(self, key) is None:
            setattr(self, key, OnnxModel(self.settings.model_dir, spec, self.settings.cpu_threads))

    def unload_model(self, key: str) -> None:
        """Release the session without deleting files; serialize against inference."""
        if key not in {spec.key for spec in SPECS}:
            raise ValueError(f"Unknown model: {key}")
        if getattr(self, key) is not None:
            setattr(self, key, None)
            gc.collect()

    def provenance(self) -> dict:
        return {
            model.spec.key: {
                "repository": model.spec.repository,
                "revision": model.spec.revision,
                "onnx_sha256": model.sha256,
                **({"variant": model.spec.variant} if model.spec.variant else {}),
            }
            for model in (getattr(self, spec.key) for spec in SPECS)
            if model is not None
        }

    def classify(
        self,
        data: bytes,
        enabled_models: tuple[str, ...] | None = None,
    ) -> Classification:
        selected = self.available_models() if enabled_models is None else enabled_models
        if not selected or set(selected) - set(self.available_models()):
            raise ValueError("No models enabled or unavailable model selected")
        selected = tuple(spec.key for spec in SPECS if spec.key in selected)
        if len(data) > self.settings.max_download_mb * 1024 * 1024:
            raise ValueError("Image exceeds byte limit")
        frames: list[dict] = []
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > self.settings.max_pixels:
                    raise ValueError("Image exceeds pixel limit")
                image_format = (image.format or "bin").lower()
                total_frames = getattr(image, "n_frames", 1)
                indices = np.linspace(
                    0,
                    total_frames - 1,
                    min(total_frames, self.settings.animation_samples),
                    dtype=int,
                )
                for index in indices:
                    image.seek(int(index))
                    frame = ImageOps.exif_transpose(image)
                    scores = {
                        f"{spec.key}_scores": getattr(self, spec.key).predict(
                            preprocess(frame, spec)
                        )
                        for spec in SPECS
                        if spec.key in selected
                    }
                    frames.append({"index": int(index), **scores})

        # Style is averaged over sampled frames; any sampled NSFW frame can flag the image.
        def average_scores(key: str) -> dict[str, float]:
            if key not in selected:
                return {}
            name = f"{key}_scores"
            return {
                label: sum(frame[name][label] for frame in frames) / len(frames)
                for label in frames[0][name]
            }

        anime_scores = average_scores("anime")
        anime_real_scores = average_scores("anime_real")
        nsfw_scores = (
            max(frames, key=lambda frame: frame["nsfw_scores"]["nsfw"])["nsfw_scores"]
            if "nsfw" in selected
            else {}
        )
        return Classification(
            **summarize(anime_scores, nsfw_scores, self.settings, anime_real_scores),
            anime_scores=anime_scores,
            nsfw_scores=nsfw_scores,
            anime_real_scores=anime_real_scores,
            enabled_models=selected,
            dimension_model="anime"
            if anime_scores
            else "anime_real"
            if anime_real_scores
            else None,
            frames=frames,
            total_frames=total_frames,
            image_format=image_format,
            width=width,
            height=height,
        )
