from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nonebot.log import logger

from .media_storage import DATA_ROOT, AdvancedMediaStorageSystem, rebuild_global_index

# ================= 实例管理 (Factory Pattern) =================

_SYSTEM_INSTANCES: dict[str, AdvancedMediaStorageSystem] = {}

def _get_system(folder_name: str) -> AdvancedMediaStorageSystem:
    """
    获取指定 folder 的存储系统实例。
    每个 folder 维护一个单独的 AdvancedMediaStorageSystem 类。
    """
    if folder_name not in _SYSTEM_INSTANCES:
        # name 参数决定了存储路径：data/advanced_media/{folder_name}
        _SYSTEM_INSTANCES[folder_name] = AdvancedMediaStorageSystem(name=folder_name)
    return _SYSTEM_INSTANCES[folder_name]

def _get_folder_config_path(folder: str) -> Path:
    """获取图库配置文件的路径"""
    return DATA_ROOT / folder / "config.json"


def _image_sequence_sort_key(image: dict[str, Any]) -> tuple[int, int | str, str]:
    image_id = str(image.get("id") or "")
    filename = str(image.get("stored_filename") or image.get("filename") or "")
    filename_stem = Path(filename).stem
    sequence_text = image_id if image_id.isdigit() else filename_stem
    if sequence_text.isdigit():
        return (0, int(sequence_text), filename_stem)
    return (1, image.get("created_at", ""), filename_stem)


def _is_visible_to_group(image: dict[str, Any], folder_visible: bool, group_id: int) -> bool:
    return (
        bool(folder_visible)
        or bool(image.get("visibility", False))
        or int(image.get("group", 0)) == group_id
    )


def _image_info_from_metadata(folder: str, image: dict[str, Any]) -> dict[str, Any]:
    contributor = (image.get("group", 0), image.get("qq", 0))
    return {
        "id": image["id"],
        "filename": image["stored_filename"],
        "thumbnail": image.get("thumbnail_filename"),
        "folder": folder,
        "original_name": image.get("original_name"),
        "contributor": contributor,
        "tags": image.get("tags", []),
        "description": image.get("description", ""),
        "visibility": image.get("visibility", False),
    }


def _visible_image_metadata(
    folder: str,
    group_id: int,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    system = _get_system(folder)
    folder_config = _manage_folder_config(folder, mode="read")
    folder_visible = bool(folder_config.get("visibility", False))
    visible_images = [
        image
        for image in system.list_files(return_type="dict")
        if _is_visible_to_group(image, folder_visible, int(group_id))
    ]
    visible_images.sort(key=_image_sequence_sort_key)
    if limit is not None and limit > 0:
        return visible_images[:limit]
    return visible_images

def _manage_folder_config(
    folder: str,
    alias: list[str] | None = None,
    visibility: bool | None = None,
    mode: str = "read",
) -> dict[str, Any]:
    """
    管理图库的额外配置，确保 folder 配置系统存在
    """
    config_path = _get_folder_config_path(folder)

    # 默认配置
    data = {'alias': [], 'visibility': False}

    # 读取现有配置
    if config_path.exists():
        try:
            with open(config_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load image folder config %s: %s", folder, exc)

    if mode == 'read':
        return data

    # 更新配置
    if alias is not None:
        data['alias'] = list(set(data['alias'] + alias)) # 合并别名
    if visibility is not None:
        data['visibility'] = visibility

    # 保存
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data

# ================= 兼容接口实现 =================

def init_folder(
    folder: str,
    alias: list[str] | None = None,
    visibility: bool = False,
) -> dict[str, Any]:
    '''
    初始化图库，返回图库metadata
    '''
    # 1. 确保系统实例存在
    aliases = alias or []
    system = _get_system(folder)

    # 2. 更新或读取图库级配置
    mode = 'write' if (aliases or visibility) else 'read'
    config = _manage_folder_config(folder, aliases, visibility, mode=mode)

    # 3. 获取文件列表
    files = system.list_files(return_type='dict')

    # 4. 构造返回值
    image_list = []
    for f in files:
        contributor = (f.get('group', 0), f.get('qq', 0))

        image_list.append({
            'id': f['id'],                   # [修改] 纯 UUID
            'filename': f['stored_filename'], # [修改] 带后缀的物理文件名
            'thumbnail': f.get('thumbnail_filename'),
            'original_name': f.get('original_name'),
            'contributor': contributor,
            'tags': f.get('tags', []),
            'description': f.get('description', ''),
            'visibility': f.get('visibility', False)
        })

    return {
        'alias': config.get('alias', []),
        'visibility': config.get('visibility', False),
        'images': image_list
    }


def get_visible_images(
    folder: str,
    group_id: int,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    '''获取指定群可见图片，顺序与图库预览图编号一致'''
    return [
        _image_info_from_metadata(folder, image)
        for image in _visible_image_metadata(folder, group_id, limit=limit)
    ]

def get_folder_list() -> list[str]:
    '''获取图库列表'''
    if not DATA_ROOT.exists():
        return []
    return [d.name for d in DATA_ROOT.iterdir() if d.is_dir()]

def get_image_file_path(id: str, folder: str) -> Path | None:
    '''获取图库图片的本地文件路径'''
    try:
        system = _get_system(folder)
        f = system[id]
    except KeyError:
        return None
    return system.files_dir / f['stored_filename']

def rebuild_image_indexes(
    folder_name: str | None = None,
    *,
    force_thumbnails: bool = False,
) -> dict[str, Any]:
    '''重建单个图库或全部图库的缩略图索引'''
    folders = [folder_name] if folder_name else get_folder_list()
    folder_indexes: dict[str, Any] = {}
    for folder in folders:
        if not folder:
            continue
        system = _get_system(folder)
        folder_indexes[folder] = system.rebuild_index(force_thumbnails=force_thumbnails)
    global_index = rebuild_global_index()
    return {"folders": folder_indexes, "global": global_index}

def get_folder_name(alias: str, create_new: bool = False) -> str | None:
    '''通过别名或者图库名获取图库文件夹名'''
    for folder in get_folder_list():
        config = _manage_folder_config(folder, mode='read')
        if alias == folder or alias in config.get('alias', []):
            return folder

    if create_new:
        init_folder(alias, visibility=True)
        return alias
    return None

def get_all_tags_with_imagedict(
    group_id: int,
    folder_name: str | None = None,
    force_load: bool = False,
) -> list[tuple[str, dict]]:
    '''获取所有图片的标签和文件名'''
    result = []
    target_folders = [folder_name] if folder_name else get_folder_list()

    for folder in target_folders:
        try:
            folder_config = _manage_folder_config(folder, mode='read')
            folder_visible = folder_config.get('visibility', False)

            system = _get_system(folder)
            files = system.list_files(return_type='dict')

            for f in files:
                img_visible = f.get('visibility', False)
                src_group_id = f.get('group', 0)
                src_qq_id = f.get('qq', 0)
                contributor = (src_group_id, src_qq_id)

                # 权限判断
                if folder_visible or img_visible or src_group_id == group_id:
                    img_info = {
                        'id': f['id'],                    # [修改] UUID
                        'filename': f['stored_filename'],  # [修改] 物理文件名
                        'thumbnail': f.get('thumbnail_filename'),
                        'folder': folder,
                        'tags': f.get('tags', []),
                        'description': f.get('description', ''),
                        'contributor': contributor
                    }
                    for tag in f.get('tags', []):
                        result.append((tag, img_info))
        except Exception as exc:
            logger.warning("Failed to read image folder %s: %s", folder, exc)
            continue

    return result

def save_image(
    image_bytes: bytes,
    image_type: str,
    folder: str,
    qq: int = 0,
    group: int = 0,
    contributor: tuple[int, int] | None = None,
    tags: list[str] | None = None,
    description: str = '',
    visibility: bool = False,
) -> dict:
    '''保存图片到指定 folder 的 System'''
    if contributor is None:
        contributor = (group, qq)
    else:
        group, qq = contributor

    image_tags = tags or []
    system = _get_system(folder)

    ext = image_type if image_type.startswith('.') else f'.{image_type}'

    result = system.upload(
        source=image_bytes,
        ext=ext,
        original_name=f"upload_{int(time.time())}{ext}",
        # Metadata
        tags=image_tags,
        description=description,
        visibility=visibility,
        group=group,
        qq=qq
    )

    return {
        'id': result['id'],                     # [修改] UUID
        'filename': result['stored_filename'],   # [修改] 物理文件名
        'status': result.get('status', 'created'),
        'contributor': contributor,
        'tags': image_tags,
        'description': description,
        'visibility': visibility,
        'folder': folder
    }

def similar_images(image_bytes: bytes, folder: str, threshold: float = 0.95) -> list[dict]:
    '''在指定 folder 中查找相似图片'''
    system = _get_system(folder)

    import hashlib
    sha256 = hashlib.sha256(image_bytes).hexdigest()

    matches = system.list_files(return_type='dict', hash=sha256)

    return [
        {
            'id': m['id'],                      # [修改] UUID
            'filename': m['stored_filename'],    # [修改] 物理文件名
            'thumbnail': m.get('thumbnail_filename'),
            'folder': folder,
            'tags': m.get('tags', []),
            'description': m.get('description', '')
        } for m in matches
    ]

def delete_image(id: str | list[str], folder: str) -> None:
    '''
    删除指定 folder 中的图片
    '''
    system = _get_system(folder)
    if isinstance(id, str):
        id = [id]
    for fid in id:
        system.delete(fid)

def get_imagedata(id: str, folder: str) -> dict | None:
    '''
    获取图片信息
    '''
    try:
        system = _get_system(folder)
        f = system[id] # 使用下标访问 (key=UUID)

        contributor = (f.get('group', 0), f.get('qq', 0))

        return {
            'id': f['id'],                      # [修改] UUID
            'filename': f['stored_filename'],    # [修改] 物理文件名
            'thumbnail': f.get('thumbnail_filename'),
            'folder': folder,
            'tags': f.get('tags', []),
            'description': f.get('description', ''),
            'contributor': contributor,
            'group': contributor[0],
            'qq': contributor[1]
        }
    except KeyError:
        return None

def update_imagedata(
    id: str,
    folder: str,
    data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    '''更新信息'''
    system = _get_system(folder)
    update_data = data.copy() if data else {}
    update_data.update(kwargs)
    system.update(id, **update_data)

def create_image_gallery(folder, groupid, images_per_row=5, limit=None) -> bytes|None:
    '''创建预览图'''
    system = _get_system(folder)
    valid_files = _visible_image_metadata(folder, int(groupid), limit=limit)
    if not valid_files:
        return None

    return system._generate_image_grid(valid_files)
