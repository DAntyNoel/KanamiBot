from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11 import Bot
from nonebot.log import logger


async def upload_group_file(
    bot: Bot,
    group_id: int,
    file_path: str | Path,
    name: str | None = None,
) -> None:
    """
    直接调用 upload_group_file API 上传到群文件。
    
    Args:
        bot: Bot 实例
        group_id: 群号
        file_path: 本地路径
        name: 上传后的显示文件名 (如果不填则使用原文件名)
    """
    path_obj = Path(file_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"File not found: {path_obj}")
    
    abs_path = str(path_obj.resolve())
    file_name = name if name else path_obj.name

    logger.info(f"Uploading file to group {group_id}: {abs_path}")
    
    # 调用 OneBot V11 的 upload_group_file 接口
    await bot.call_api(
        "upload_group_file",
        group_id=group_id,
        file=abs_path, # 这里通常传本地路径
        name=file_name,
    )
