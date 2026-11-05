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
    cpu_threads: int = Field(default=2, ge=1, le=64)
    queue_size: int = Field(default=512, ge=1)
    max_download_mb: int = Field(default=20, ge=1, le=100)
    max_pixels: int = Field(default=25_000_000, ge=1)
    animation_samples: int = Field(default=8, ge=1, le=32)
    local_roots: list[Path] = Field(default_factory=list)
    saved_dirs: dict[str, Path] = Field(default_factory=lambda: {"default": ROOT / "saved"})
    preview_dimensions: set[str] = Field(default_factory=lambda: {"anime", "real", "unknown"})
    preview_safety: set[str] = Field(
        default_factory=lambda: {"normal", "suggestive", "nsfw", "unknown"}
    )
    preview_categories_a: set[str] = Field(default_factory=lambda: {"A1", "A2", "A3"})
    preview_categories_b: set[str] = Field(default_factory=lambda: {"B1", "B2", "B3", "unknown"})
    preview_page_size: int = Field(default=20, ge=1, le=40)
    inference_cache_size: int = Field(default=512, ge=1)
