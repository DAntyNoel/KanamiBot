from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .classifier import Classification

RETENTION = timedelta(days=3)
_RECORD_NAME = re.compile(r"\d{8}T\d{12}Z_[0-9a-f]{32}")


class ResultStore:
    """Each record is an atomic directory containing original bytes and result.json."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()

    def save(
        self,
        data: bytes,
        result: Classification,
        *,
        source: dict,
        models: dict,
        thresholds: dict,
        now: datetime | None = None,
    ) -> Path:
        saved_at = (now or datetime.now(UTC)).astimezone(UTC)
        record_id = f"{saved_at:%Y%m%dT%H%M%S%fZ}_{uuid.uuid4().hex}"
        parent = self.root / result.dimension / result.subtype / result.safety
        target = parent / record_id
        extension = {"jpeg": "jpg"}.get(result.image_format, result.image_format)
        if not re.fullmatch(r"[a-z0-9]+", extension):
            extension = "bin"
        metadata = {
            "schema_version": 2,
            "saved_at": saved_at.isoformat(),
            "expires_at": (saved_at + RETENTION).isoformat(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "image_file": f"image.{extension}",
            "source": source,
            "models": models,
            "thresholds": thresholds,
            "classification": result.to_dict(),
        }
        with self._lock:
            parent.mkdir(parents=True, exist_ok=True)
            staging = parent / f".pending-{record_id}"
            staging.mkdir()
            try:
                (staging / f"image.{extension}").write_bytes(data)
                (staging / "result.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(staging, target)
            except BaseException:
                shutil.rmtree(staging)
                raise
        return target / "result.json"

    def cleanup(self, *, now: datetime | None = None) -> int:
        """Use the record's immutable UTC filename, even if result.json is corrupt."""
        cutoff = (now or datetime.now(UTC)).astimezone(UTC) - RETENTION
        deleted = 0
        with self._lock:
            if not self.root.exists():
                return 0
            # Only visit our known category hierarchy; never follow directory symlinks.
            for dimension, subtypes in (
                ("anime", ("anime_adult", "anime_contentious", "anime_non_adult", "unspecified")),
                ("real", ("non_anime",)),
                ("unknown", ("unspecified",)),
            ):
                for subtype in subtypes:
                    for safety in ("normal", "nsfw", "unknown"):
                        parent = self.root / dimension / subtype / safety
                        if any(
                            p.is_symlink() for p in (parent, parent.parent, parent.parent.parent)
                        ):
                            continue
                        if not parent.is_dir():
                            continue
                        for record in parent.iterdir():
                            name = record.name.removeprefix(".pending-")
                            if record.is_symlink() or not record.is_dir():
                                continue
                            if not _RECORD_NAME.fullmatch(name):
                                continue
                            try:
                                timestamp = datetime.strptime(
                                    name.split("_")[0], "%Y%m%dT%H%M%S%fZ"
                                ).replace(tzinfo=UTC)
                            except ValueError:
                                continue
                            if timestamp <= cutoff:
                                shutil.rmtree(record)
                                deleted += 1
        return deleted
