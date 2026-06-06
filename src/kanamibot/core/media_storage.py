from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import shutil
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from nonebot.log import logger

from .paths import DATA_DIR, DEFAULT_FONT_PATH

# 尝试导入 PIL
try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL is not installed. Image processing features are disabled.")

DATA_ROOT = DATA_DIR / "advanced_media"
FONT_PATH = DEFAULT_FONT_PATH
FOLDER_INDEX_NAME = "index.json"
GLOBAL_INDEX_PATH = DATA_ROOT / FOLDER_INDEX_NAME
THUMB_SIZE = (320, 320)


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(f".tmp.{uuid.uuid4()}")
    try:
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_file, path)
    except Exception:
        temp_file.unlink(missing_ok=True)
        raise


def _normalize_extension(ext: str | None) -> str:
    if not ext:
        return ".bin"
    normalized = ext.lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    if normalized == ".jpeg":
        return ".jpg"
    return normalized


def _metadata_sort_key(meta: dict[str, Any]) -> str:
    return str(meta.get("created_at", ""))


def rebuild_global_index() -> dict[str, Any]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    folders: dict[str, Any] = {}

    for folder_path in sorted(DATA_ROOT.iterdir(), key=lambda path: path.name):
        if not folder_path.is_dir():
            continue
        folder_index_path = folder_path / FOLDER_INDEX_NAME
        if not folder_index_path.exists():
            continue
        try:
            with folder_index_path.open(encoding="utf-8") as file:
                folder_index = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load image index %s: %s", folder_path.name, exc)
            continue
        folders[folder_path.name] = folder_index

    payload = {
        "version": 1,
        "generated_at": datetime.datetime.now().isoformat(),
        "folder_count": len(folders),
        "image_count": sum(
            int(folder.get("image_count", 0))
            for folder in folders.values()
            if isinstance(folder, dict)
        ),
        "folders": folders,
    }
    _atomic_write_json(GLOBAL_INDEX_PATH, payload)
    return payload


class AdvancedMediaStorageSystem:
    """
    增强版文件存储交互系统
    支持多种输入源：File Path, bytes, BytesIO, PIL.Image
    """
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
    ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | {'.mp3', '.mp4', '.wav', '.ogg'}
    
    def __init__(self, name: str = "gallery"):
        self.storage_root = DATA_ROOT / name
        self.files_dir = self.storage_root / "files"
        self.thumbs_dir = self.storage_root / "thumbs"
        self.metadata_path = self.storage_root / "metadata.json"
        self.index_path = self.storage_root / FOLDER_INDEX_NAME
        
        self._initialize_storage()
        self.metadata_registry = self._load_metadata()

    def _initialize_storage(self):
        if not self.files_dir.exists():
            self.files_dir.mkdir(parents=True, exist_ok=True)
        if not self.thumbs_dir.exists():
            self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            _atomic_write_json(self.metadata_path, {})

    def _load_metadata(self) -> dict:
        try:
            with open(self.metadata_path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_metadata(self):
        _atomic_write_json(self.metadata_path, self.metadata_registry)

    def _calculate_hash_from_bytes(self, data: bytes) -> str:
        sha256 = hashlib.sha256()
        sha256.update(data)
        return sha256.hexdigest()

    def _calculate_hash_from_file(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _select_file_id(self, file_hash: str) -> str:
        for length in (16, 20, 24, 32, 40, 64):
            candidate = file_hash[:length]
            existing = self.metadata_registry.get(candidate)
            if existing is None or existing.get("hash") == file_hash:
                return candidate
        return file_hash

    def _find_existing_by_hash(self, file_hash: str | None) -> tuple[str, dict] | None:
        if not file_hash:
            return None
        for file_id, meta in self.metadata_registry.items():
            if meta.get("hash") == file_hash:
                return file_id, meta
        return None

    def _media_path_for(self, meta: dict, *, prefer_thumb: bool = False) -> Path:
        if prefer_thumb:
            thumb_name = meta.get("thumbnail_filename") or f"{meta.get('id')}.webp"
            thumb_path = self.thumbs_dir / str(thumb_name)
            if thumb_path.exists():
                return thumb_path
        return self.files_dir / meta["stored_filename"]

    def _ensure_thumbnail(self, meta: dict, *, force: bool = False) -> str | None:
        if not PIL_AVAILABLE:
            return None
        ext = _normalize_extension(meta.get("file_type", "")).lower()
        if ext not in self.IMAGE_EXTENSIONS:
            return None

        file_id = str(meta.get("id") or "")
        if not file_id:
            return None
        thumb_name = f"{file_id}.webp"
        thumb_path = self.thumbs_dir / thumb_name
        if thumb_path.exists() and not force:
            return thumb_name

        source_path = self.files_dir / meta["stored_filename"]
        if not source_path.exists():
            return None

        try:
            with Image.open(source_path) as image:
                try:
                    image.seek(0)
                except EOFError:
                    pass
                image = ImageOps.exif_transpose(image)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA")
                image.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(thumb_path, format="WEBP", quality=82, method=4)
        except Exception as exc:
            logger.warning("Failed to create thumbnail for %s: %s", source_path, exc)
            return None
        return thumb_name

    def _index_entry(self, meta: dict) -> dict[str, Any]:
        thumb_name = meta.get("thumbnail_filename")
        return {
            "id": meta.get("id"),
            "filename": meta.get("stored_filename"),
            "file": f"files/{meta.get('stored_filename')}",
            "thumbnail": f"thumbs/{thumb_name}" if thumb_name else None,
            "file_size": meta.get("file_size", 0),
            "file_type": meta.get("file_type", "unknown"),
            "hash": meta.get("hash"),
            "created_at": meta.get("created_at"),
            "tags": meta.get("tags", []),
            "description": meta.get("description", ""),
            "visibility": meta.get("visibility", False),
            "group": meta.get("group", 0),
            "qq": meta.get("qq", 0),
            "original_name": meta.get("original_name"),
            "legacy_id": meta.get("legacy_id"),
            "legacy_filename": meta.get("legacy_filename"),
        }

    def rebuild_index(self, *, force_thumbnails: bool = False) -> dict[str, Any]:
        changed = False
        entries: list[dict[str, Any]] = []

        for meta in sorted(self.metadata_registry.values(), key=_metadata_sort_key, reverse=True):
            thumb_name = self._ensure_thumbnail(meta, force=force_thumbnails)
            if thumb_name and meta.get("thumbnail_filename") != thumb_name:
                meta["thumbnail_filename"] = thumb_name
                changed = True
            entries.append(self._index_entry(meta))

        if changed:
            self._save_metadata()

        payload = {
            "version": 1,
            "folder": self.storage_root.name,
            "generated_at": datetime.datetime.now().isoformat(),
            "image_count": len(entries),
            "images": entries,
        }
        _atomic_write_json(self.index_path, payload)
        return payload

    def _refresh_indexes(self, *, force_thumbnails: bool = False) -> None:
        self.rebuild_index(force_thumbnails=force_thumbnails)
        rebuild_global_index()

    # --- 核心功能：下标访问 ---
    def __getitem__(self, key: str | int | slice) -> dict | list[dict]:
        if isinstance(key, str):
            if key not in self.metadata_registry:
                raise KeyError(f"File ID '{key}' not found.")
            return self.metadata_registry[key]

        # 转换为列表以支持下标，按创建时间排序
        ordered_files = sorted(
            self.metadata_registry.values(),
            key=lambda x: x.get('created_at', ''),
        )
        
        if isinstance(key, int) or isinstance(key, slice):
            return ordered_files[key]
        else:
            raise TypeError("Invalid argument type. Must be str (UUID), int (Index), or slice.")

    # --- 核心功能：生成网格图 ---
    def _generate_image_grid(self, metadata_list: list[dict], thumb_size=(200, 200)) -> bytes:
        if not PIL_AVAILABLE:
            return b""

        image_metas = [
            m for m in metadata_list if ("." + m.get('file_type', '')) in self.IMAGE_EXTENSIONS
        ]
        count = len(image_metas)
        if count == 0:
            return b""

        cols = math.ceil(math.sqrt(count))
        cols = min(cols, 5) # 限制最大列数
        rows = math.ceil(count / cols)
        
        thumb_w, thumb_h = thumb_size
        text_area_h = 40
        padding = 15
        
        cell_w, cell_h = thumb_w, thumb_h + text_area_h
        canvas_w = cols * (cell_w + padding) + padding
        canvas_h = rows * (cell_h + padding) + padding
        
        grid_image = Image.new('RGB', (canvas_w, canvas_h), color='#f0f0f0')
        draw = ImageDraw.Draw(grid_image)
        try:
            font = ImageFont.truetype(str(FONT_PATH), size=16)
        except OSError:
            font = ImageFont.load_default()

        for i, meta in enumerate(image_metas):
            file_path = self._media_path_for(meta, prefer_thumb=True)
            row_idx, col_idx = i // cols, i % cols
            cell_x = padding + col_idx * (cell_w + padding)
            cell_y = padding + row_idx * (cell_h + padding)
            
            try:
                with Image.open(file_path) as img:
                    img = img.convert("RGB")
                    img.thumbnail(thumb_size, Image.Resampling.LANCZOS)
                    actual_w, actual_h = img.size
                    offset_x = (cell_w - actual_w) // 2
                    offset_y = (thumb_h - actual_h) // 2
                    grid_image.paste(img, (cell_x + offset_x, cell_y + offset_y))
            except Exception:
                draw.rectangle([cell_x, cell_y, cell_x+thumb_w, cell_y+thumb_h], fill="gray")

            display_text = ''
            # display_text = meta.get('description') or meta.get('original_name', 'Unknown')
            # if len(display_text) > 12: display_text = display_text[:11] + "..."
            
            text_str = f"[{i+1}] {display_text}"
            bbox = draw.textbbox((0, 0), text_str, font=font)
            text_w = bbox[2] - bbox[0]
            draw.text(
                (cell_x + (cell_w - text_w) // 2, cell_y + thumb_h + 5),
                text_str,
                fill='black',
                font=font,
            )

        output = BytesIO()
        if count > 50:
            grid_image = grid_image.resize((canvas_w // 2, canvas_h // 2))
        grid_image.save(output, format='PNG')
        return output.getvalue()

    # --- 核心功能：增强版上传 (支持多类型) ---
    def upload(self, 
               source: str | Path | bytes | BytesIO | Image.Image,
               ext: str | None = None,
               original_name: str | None = None,
               **kwargs) -> dict:
        """
        :param source: 文件路径、bytes数据、BytesIO流或PIL Image对象
        :param ext: 显式指定文件后缀 (如 .png, .jpg)，如果传入的是 bytes/stream 建议指定
        :param original_name: 原始文件名，用于元数据记录
        """
        
        data_to_write = None  # 如果需要在内存中处理数据，存放在此
        file_path_to_copy = None # 如果是现有文件，存路径
        file_hash = None
        file_size = 0
        
        # 1. 数据预处理与Hash计算
        if isinstance(source, (str, Path)):
            p = Path(source)
            if not p.exists():
                raise FileNotFoundError(f"Source {p} not found")
            file_path_to_copy = p
            file_hash = self._calculate_hash_from_file(p)
            file_size = p.stat().st_size
            if not ext:
                ext = p.suffix.lower()
            if not original_name:
                original_name = p.name

        elif isinstance(source, bytes):
            data_to_write = source
            file_hash = self._calculate_hash_from_bytes(source)
            file_size = len(source)
            if not ext:
                ext = '.bin' # 默认后缀
            if not original_name:
                original_name = f'bytes_{file_hash[:8]}{ext}'

        elif isinstance(source, BytesIO):
            data_to_write = source.getvalue()
            file_hash = self._calculate_hash_from_bytes(data_to_write)
            file_size = len(data_to_write)
            if not ext:
                ext = '.bin'
            if not original_name:
                original_name = f'stream_{file_hash[:8]}{ext}'

        elif PIL_AVAILABLE and isinstance(source, Image.Image):
            # 将 PIL 图片转为 bytes
            output = BytesIO()
            # 默认转为 PNG，除非 format 属性存在
            save_format = source.format if source.format else 'PNG'
            source.save(output, format=save_format)
            data_to_write = output.getvalue()
            
            file_hash = self._calculate_hash_from_bytes(data_to_write)
            file_size = len(data_to_write)
            
            # 确定后缀
            if not ext:
                ext = f".{save_format.lower()}"
            if not original_name:
                original_name = f'image_{file_hash[:8]}{ext}'
        else:
            raise TypeError(f"Unsupported source type: {type(source)}")

        # 确保 ext 有点号
        ext = _normalize_extension(ext)

        # 2. Hash 去重检查
        existing = self._find_existing_by_hash(file_hash)
        if existing:
            file_id, meta = existing
            return {**meta, "status": "exists", "file_id": file_id}

        # 3. 写入存储
        unique_id = self._select_file_id(file_hash or str(uuid.uuid4()))
        stored_filename = f"{unique_id}{ext}"
        destination_path = self.files_dir / stored_filename

        if file_path_to_copy:
            self.files_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path_to_copy, destination_path)
        elif data_to_write:
            with open(destination_path, 'wb') as f:
                f.write(data_to_write)

        # 4. 注册元数据
        metadata = {
            "id": unique_id,
            "original_name": original_name,
            "stored_filename": stored_filename,
            "file_size": file_size,
            "file_type": ext.lstrip('.') if ext else 'unknown',
            "hash": file_hash,
            "created_at": datetime.datetime.now().isoformat(),
            **kwargs 
        }
        self.metadata_registry[unique_id] = metadata
        self._save_metadata()
        self._refresh_indexes()
        return {**metadata, "status": "created", "file_id": unique_id}

    def delete(self, file_id: str) -> bool:
        if file_id not in self.metadata_registry:
            logger.warning("[MediaStorage] Delete failed: id not found: %s", file_id)
            return False
        meta = self.metadata_registry[file_id]
        file_path = self.files_dir / meta['stored_filename']
        if file_path.exists():
            os.remove(file_path)
        thumb_name = meta.get("thumbnail_filename") or f"{file_id}.webp"
        (self.thumbs_dir / str(thumb_name)).unlink(missing_ok=True)
        del self.metadata_registry[file_id]
        self._save_metadata()
        self._refresh_indexes()
        return True
    
    def update(self, file_id: str, **kwargs):
        if file_id in self.metadata_registry:
            self.metadata_registry[file_id].update(kwargs)
            self._save_metadata()
            self._refresh_indexes()

    def list_files(self, 
                   return_type: Literal['dict', 'image'] = 'image', 
                   limit: int | None = None,
                   tag_filter: str | None = None,
                   **kwargs) -> list[dict] | bytes:
        '''通过kwargs方式可以筛选'''
        
        all_meta = list(self.metadata_registry.values())
        all_meta.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        filtered = []
        for m in all_meta:
            if tag_filter and tag_filter not in m.get('tags', []):
                continue
            if kwargs:
                is_match = True
                for k, v in kwargs.items():
                    # Loose mode: If not exist then pass
                    if m.get(k, v) != v:
                        is_match = False
                        break
                if not is_match:
                    continue

            filtered.append(m)
            
        if limit is not None and limit > 0:
            filtered = filtered[:limit]
            
        if return_type == 'dict':
            return filtered
        else:
            return self._generate_image_grid(filtered)
