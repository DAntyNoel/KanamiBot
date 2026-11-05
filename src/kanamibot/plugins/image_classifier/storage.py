from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .classifier import RULES, Classification, summarize
from .config import Settings

RETENTION = timedelta(days=2)
_RECORD_NAME = re.compile(r"\d{8}T\d{12}Z_[0-9a-f]{32}")


class ResultStore:
    """Atomic records with either an owned image or an external read-only reference."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._images: dict[str, Path] = {}

    def reviewed_before(self, user: str, cutoff: datetime) -> set[str]:
        with self._review_db() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT digest FROM delivered WHERE user = ? AND delivered_at < ?",
                    (user, cutoff.astimezone(UTC).isoformat()),
                )
            }

    def mark_reviewed(self, user: str, digests: list[str], now: datetime) -> None:
        with self._review_db() as db:
            db.executemany(
                "INSERT OR IGNORE INTO delivered(user, digest, delivered_at) VALUES (?, ?, ?)",
                [(user, digest, now.astimezone(UTC).isoformat()) for digest in digests],
            )

    @contextmanager
    def _review_db(self):
        # Metadata only; keep delivery history outside per-image retention directories.
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "review_history.sqlite3", timeout=30)
        try:
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS delivered (
                    user TEXT NOT NULL, digest TEXT NOT NULL, delivered_at TEXT NOT NULL,
                    PRIMARY KEY (user, digest))""")
                yield db
        finally:
            db.close()

    def save(
        self,
        data: bytes,
        result: Classification,
        *,
        source: dict,
        models: dict,
        thresholds: dict,
        now: datetime | None = None,
        reference: Path | None = None,
        inference_settings: dict | None = None,
    ) -> Path:
        saved_at = (now or datetime.now(UTC)).astimezone(UTC)
        record_id = f"{saved_at:%Y%m%dT%H%M%S%fZ}_{uuid.uuid4().hex}"
        parent = self.root / result.dimension / result.subtype / result.safety
        target = parent / record_id
        extension = {"jpeg": "jpg"}.get(result.image_format, result.image_format)
        if not re.fullmatch(r"[a-z0-9]+", extension):
            extension = "bin"
        metadata = {
            "schema_version": 4,
            "saved_at": saved_at.isoformat(),
            "expires_at": (saved_at + RETENTION).isoformat(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "image_file": f"image.{extension}",
            "image_reference": str(reference) if reference else None,
            "source": source,
            "models": models,
            "thresholds": thresholds,
            "inference_settings": inference_settings,
            "classification": result.to_dict(),
        }
        with self._lock:
            parent.mkdir(parents=True, exist_ok=True)
            staging = parent / f".pending-{record_id}"
            staging.mkdir()
            try:
                if reference is None:
                    cached = self._images.get(metadata["sha256"])
                    try:
                        if cached is None:
                            raise FileNotFoundError
                        os.link(cached, staging / metadata["image_file"])
                    except OSError:
                        (staging / metadata["image_file"]).write_bytes(data)
                (staging / "result.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(staging, target)
                if reference is None:
                    self._images[metadata["sha256"]] = target / metadata["image_file"]
            except BaseException:
                shutil.rmtree(staging)
                raise
        return target / "result.json"

    def rebuild_index(self) -> list[tuple[Path, dict]]:
        records = self.records()
        with self._lock:
            for path, record in records:
                if not record.get("image_reference"):
                    image = path.parent / record["image_file"]
                    if image.is_file():
                        self._images.setdefault(record["sha256"], image)
        return records

    def records(self, *, now: datetime | None = None) -> list[tuple[Path, dict]]:
        """Ignore expired/corrupt records even between physical cleanup runs."""
        current = now or datetime.now(UTC)
        settings = Settings()
        records = []
        with self._lock:
            for path in self.root.glob("*/*/*/*/result.json"):
                try:
                    if any(p.is_symlink() for p in (path, *path.parents) if p != self.root):
                        continue
                    record = json.loads(path.read_text(encoding="utf-8"))
                    # Filename is authoritative, also for legacy 72h metadata.
                    saved = datetime.strptime(
                        path.parent.name.split("_")[0], "%Y%m%dT%H%M%S%fZ"
                    ).replace(tzinfo=UTC)
                    if saved + RETENTION > current:
                        classification = record["classification"]
                        classification.update(
                            summarize(
                                classification["anime_scores"],
                                classification["nsfw_scores"],
                                settings,
                                classification.get("anime_real_scores"),
                            )
                        )
                        record["thresholds"] = RULES.copy()
                        record["schema_version"] = 4
                        records.append((path, record))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return sorted(records, key=lambda item: item[1]["saved_at"], reverse=True)

    @staticmethod
    def image_bytes(path: Path, record: dict, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        image = (
            Path(record["image_reference"])
            if record.get("image_reference")
            else (path.parent / record["image_file"])
        )
        with image.open("rb") as file:
            data = file.read(max_bytes + 1)
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Cached image changed or exceeds limit")
        return data

    def archive(self, token: str, destination: Path) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", token):
            raise ValueError("图片编号应为预览中的16位编号")
        matches = [(p, r) for p, r in self.records() if r["sha256"].startswith(token)]
        if not matches:
            raise ValueError("图片已过期或不存在")
        if len({r["sha256"] for _, r in matches}) != 1:
            raise ValueError("图片编号冲突")
        path, record = matches[0]
        # Snapshot bytes before cleanup can unlink the temporary record.
        with self._lock:
            data = self.image_bytes(path, record)
            destination = destination.resolve()
            if destination.is_relative_to(self.root):
                raise ValueError("永久保存目录不能位于临时目录内")
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / record["sha256"]
            if target.exists():
                existing = json.loads((target / "result.json").read_text(encoding="utf-8"))
                self.image_bytes(target / "result.json", existing)
                return target
            staging = destination / f".pending-{uuid.uuid4().hex}"
            staging.mkdir()
            try:
                extension = record["classification"]["image_format"]
                if not re.fullmatch(r"[a-z0-9]+", extension):
                    extension = "bin"
                name = f"image.{extension}"
                (staging / name).write_bytes(data)
                permanent = record | {
                    "image_reference": None,
                    "image_file": name,
                    "expires_at": None,
                    "archived_at": datetime.now(UTC).isoformat(),
                }
                (staging / "result.json").write_text(
                    json.dumps(permanent, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(staging, target)
            except BaseException:
                shutil.rmtree(staging)
                raise
            return target

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
                    for safety in ("normal", "suggestive", "nsfw", "unknown"):
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
            self._images = {k: p for k, p in self._images.items() if p.exists()}
        return deleted
