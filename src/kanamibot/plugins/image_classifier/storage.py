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
            db.executemany(
                "INSERT OR IGNORE INTO review_visible SELECT user, day, a, b, position "
                "FROM review_numbers WHERE user=? AND digest=?",
                [(user, digest) for digest in digests],
            )

    def mark_previewed(self, user: str, digests: list[str]) -> None:
        """Remember shown numbers without changing nightly delivery statistics."""
        with self._review_db() as db:
            db.executemany(
                "INSERT OR IGNORE INTO review_visible SELECT user, day, a, b, position "
                "FROM review_numbers WHERE user=? AND digest=?",
                [(user, digest) for digest in digests],
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
                db.execute("""CREATE TABLE IF NOT EXISTS review_numbers (
                    user TEXT NOT NULL, day TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL,
                    position INTEGER NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (user, day, a, b, position), UNIQUE (user, day, a, b, digest))""")
                db.execute("""CREATE TABLE IF NOT EXISTS review_visible (
                    user TEXT NOT NULL, day TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL,
                    position INTEGER NOT NULL, PRIMARY KEY (user, day, a, b, position))""")
                yield db
        finally:
            db.close()

    def number_review(self, user: str, day: str, groups: dict) -> None:
        """Append numbers; never shift a previously displayed position when new images arrive."""
        with self._review_db() as db:
            db.execute("BEGIN IMMEDIATE")
            for (a, b), records in groups.items():
                existing = {
                    digest: pos
                    for pos, digest in db.execute(
                        "SELECT position, digest FROM review_numbers "
                        "WHERE user=? AND day=? AND a=? AND b=?",
                        (user, day, a, b),
                    )
                }
                position = max(existing.values(), default=0)
                for _, record in records:
                    digest = record["sha256"]
                    if digest not in existing:
                        position += 1
                        db.execute(
                            "INSERT INTO review_numbers VALUES (?, ?, ?, ?, ?, ?)",
                            (user, day, a, b, position, digest),
                        )
                        existing[digest] = position
                    record["review_number"] = existing[digest]
                records.sort(key=lambda item: item[1]["review_number"])

    def numbered_images(self, user: str, category: tuple[str, str], numbers: list[int]):
        """Resolve against the most recent displayed group, including yesterday after midnight."""
        a, b = category
        with self._review_db() as db:
            day = db.execute(
                "SELECT MAX(day) FROM review_visible WHERE user=? AND a=? AND b=?", (user, a, b)
            ).fetchone()[0]
            if day is None:
                raise ValueError("尚无该组预览，请先发送对应的图分组指令查看缩略图。")
            mapping = dict(
                db.execute(
                    "SELECT position, digest FROM review_numbers "
                    "WHERE user=? AND day=? AND a=? AND b=?",
                    (user, day, a, b),
                )
            )
        available = {}
        for path, record in self.records():
            available.setdefault(record["sha256"], (path, record))
        return [(number, available.get(mapping.get(number))) for number in numbers]

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
