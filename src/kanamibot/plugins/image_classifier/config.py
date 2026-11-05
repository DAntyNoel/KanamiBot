from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[4] / "data" / "image_classifier"


class Settings(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: f"image_classifier_{name}",
        populate_by_name=True,
        extra="ignore",
    )

    enabled: bool = True
    anime_enabled: bool = True
    anime_real_enabled: bool = True
    nsfw_enabled: bool = True
    model_dir: Path = ROOT / "models"
    cache_dir: Path = ROOT / "tmp"
    anime_threshold: float = Field(default=0.5, gt=0, lt=1)
    nsfw_threshold: float = Field(default=0.5, gt=0, lt=1)
    cpu_threads: int = Field(default=2, ge=1, le=64)
    queue_size: int = Field(default=512, ge=1)
    max_download_mb: int = Field(default=20, ge=1, le=100)
    max_pixels: int = Field(default=25_000_000, ge=1)
    animation_samples: int = Field(default=8, ge=1, le=32)
