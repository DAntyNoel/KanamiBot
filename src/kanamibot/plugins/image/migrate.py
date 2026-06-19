from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from nonebot.log import logger

from kanamibot.core.media_storage import (
    DATA_ROOT,
    AdvancedMediaStorageSystem,
    _atomic_write_json,
    _normalize_extension,
    rebuild_global_index,
)

DEFAULT_OLD_MEDIA_ROOT = Path(r"D:\DAntyNoel\Kanami-NB\data\advanced_media")
NamingStrategy = Literal["hash", "sequential"]


def _load_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def _calculate_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _select_file_id(file_hash: str, metadata: dict[str, Any]) -> str:
    for length in (16, 20, 24, 32, 40, 64):
        candidate = file_hash[:length]
        existing = metadata.get(candidate)
        if existing is None or existing.get("hash") == file_hash:
            return candidate
    return file_hash


def _select_sequential_id(sequence: int, width: int, metadata: dict[str, Any]) -> str:
    candidate = f"{sequence:0{width}d}"
    if candidate not in metadata:
        return candidate

    next_sequence = sequence + 1
    while True:
        candidate = f"{next_sequence:0{width}d}"
        if candidate not in metadata:
            return candidate
        next_sequence += 1


def _find_existing_by_hash(
    file_hash: str,
    metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    for image_id, meta in metadata.items():
        if isinstance(meta, dict) and meta.get("hash") == file_hash:
            return image_id, meta
    return None


def _folder_names(source_root: Path, folders: Iterable[str] | None) -> list[str]:
    if folders is not None:
        return [str(folder) for folder in folders]
    if not source_root.exists():
        return []
    return [path.name for path in sorted(source_root.iterdir(), key=lambda item: item.name)]


def _safe_remove_tree(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError(f"refusing to remove path outside target root: {path}")
    if resolved_path.exists():
        shutil.rmtree(resolved_path)


def _source_items(source_metadata: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items = [
        (str(key), value)
        for key, value in source_metadata.items()
        if isinstance(value, dict)
    ]
    return sorted(
        items,
        key=lambda item: (
            str(item[1].get("created_at", "")),
            str(item[1].get("stored_filename", "")),
            item[0],
        ),
    )


def _source_file_for(folder_path: Path, meta: dict[str, Any]) -> Path | None:
    filename = meta.get("stored_filename") or meta.get("filename") or meta.get("file")
    if not filename:
        return None
    return folder_path / "files" / str(filename)


def migrate(
    source_root: str | Path = DEFAULT_OLD_MEDIA_ROOT,
    folders: Iterable[str] | None = None,
    *,
    dry_run: bool = True,
    overwrite: bool = False,
    rebuild_thumbnails: bool = True,
    naming_strategy: NamingStrategy = "hash",
    replace_target: bool = False,
) -> dict[str, Any]:
    """Migrate old advanced_media folders into the new layout.

    The old repository is only read. Runtime image data remains under this repo's
    ``data/advanced_media`` and is ignored by Git.
    """
    if naming_strategy not in {"hash", "sequential"}:
        raise ValueError("naming_strategy must be 'hash' or 'sequential'")

    source_root = Path(source_root)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "naming_strategy": naming_strategy,
        "replace_target": replace_target,
        "source_root": str(source_root),
        "target_root": str(DATA_ROOT),
        "folders": {},
        "created": 0,
        "skipped": 0,
        "missing": 0,
        "errors": 0,
    }

    if not source_root.exists():
        report["errors"] = 1
        report["error"] = f"source root not found: {source_root}"
        return report

    for folder_name in _folder_names(source_root, folders):
        source_folder = source_root / folder_name
        if not source_folder.is_dir():
            continue

        target_folder = DATA_ROOT / folder_name
        target_files = target_folder / "files"
        target_metadata_path = target_folder / "metadata.json"
        target_config_path = target_folder / "config.json"

        source_metadata = _load_json(source_folder / "metadata.json", {})
        if not isinstance(source_metadata, dict):
            source_metadata = {}

        if replace_target and not dry_run:
            _safe_remove_tree(target_folder, DATA_ROOT)

        target_metadata = {} if replace_target and dry_run else _load_json(target_metadata_path, {})
        if not isinstance(target_metadata, dict):
            target_metadata = {}

        source_items = _source_items(source_metadata)
        sequence_width = max(4, len(str(len(source_items))))
        folder_report = {
            "created": 0,
            "skipped": 0,
            "missing": 0,
            "errors": [],
            "config_copied": False,
        }

        for sequence, (legacy_key, raw_meta) in enumerate(source_items, start=1):
            if not isinstance(raw_meta, dict):
                folder_report["errors"].append(f"invalid metadata entry: {legacy_key}")
                continue

            source_file = _source_file_for(source_folder, raw_meta)
            if source_file is None or not source_file.exists():
                folder_report["missing"] += 1
                continue

            try:
                file_hash = str(raw_meta.get("hash") or _calculate_hash(source_file))
                existing = _find_existing_by_hash(file_hash, target_metadata)
                if existing and not overwrite:
                    folder_report["skipped"] += 1
                    continue

                if naming_strategy == "sequential":
                    image_id = _select_sequential_id(sequence, sequence_width, target_metadata)
                else:
                    image_id = (
                        existing[0]
                        if existing
                        else _select_file_id(file_hash, target_metadata)
                    )

                ext = _normalize_extension(source_file.suffix or raw_meta.get("file_type"))
                stored_filename = f"{image_id}{ext}"
                file_size = source_file.stat().st_size
                legacy_id = str(raw_meta.get("id") or legacy_key)

                migrated_meta = {
                    **raw_meta,
                    "id": image_id,
                    "stored_filename": stored_filename,
                    "file_size": file_size,
                    "file_type": ext.lstrip("."),
                    "hash": file_hash,
                    "created_at": raw_meta.get("created_at")
                    or datetime.datetime.fromtimestamp(source_file.stat().st_mtime).isoformat(),
                    "legacy_id": legacy_id,
                    "legacy_filename": raw_meta.get("stored_filename"),
                    "sequence": sequence if naming_strategy == "sequential" else None,
                    "migrated_at": datetime.datetime.now().isoformat(),
                }

                if not dry_run:
                    target_files.mkdir(parents=True, exist_ok=True)
                    target_path = target_files / stored_filename
                    if overwrite or not target_path.exists():
                        shutil.copy2(source_file, target_path)
                    target_metadata[image_id] = migrated_meta

                folder_report["created"] += 1
            except Exception as exc:
                logger.warning("Failed to migrate image %s/%s: %s", folder_name, legacy_key, exc)
                folder_report["errors"].append(f"{legacy_key}: {exc}")

        if not dry_run:
            target_folder.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(target_metadata_path, target_metadata)

            source_config_path = source_folder / "config.json"
            if source_config_path.exists() and (overwrite or not target_config_path.exists()):
                target_config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_config_path, target_config_path)
                folder_report["config_copied"] = True

            storage = AdvancedMediaStorageSystem(name=folder_name)
            storage.metadata_registry = storage._load_metadata()
            storage.rebuild_index(force_thumbnails=rebuild_thumbnails)

        report["folders"][folder_name] = folder_report
        report["created"] += int(folder_report["created"])
        report["skipped"] += int(folder_report["skipped"])
        report["missing"] += int(folder_report["missing"])
        report["errors"] += len(folder_report["errors"])

    if not dry_run:
        rebuild_global_index()

    return report
