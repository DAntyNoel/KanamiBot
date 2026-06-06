from __future__ import annotations

import imghdr
import io

import httpx
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from PIL import Image as PILImage

# 调试开关
DEBUG = False

# --- 辅助函数：提取图片 ---
async def download_all_images_from_event(event: GroupMessageEvent) -> list[bytes]:
    """尝试从 引用回复、转发消息 或 当前消息 中提取图片 (自动下载 bytes)"""
    image_list = []
    
    # 辅助内部函数：获取消息段中的图片并下载
    async def _get_imgs(message: Message, client: httpx.AsyncClient):
        imgs = []
        for seg in message:
            if seg.type == "image":
                url = seg.data.get("url")
                if url:
                    try:
                        # NoneBot Segment 不直接存储 bytes，需要下载
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            imgs.append(resp.content)
                    except httpx.HTTPError as exc:
                        logger.warning("下载图片失败: %s", exc)
            # 递归处理转发消息
            elif seg.type == "forward":
                # 转发消息的内容在 data['content'] 中，是一个消息列表
                forward_content = seg.data.get("content", [])
                if isinstance(forward_content, list):
                    for forward_msg in forward_content:
                        if isinstance(forward_msg, Message):
                            imgs.extend(await _get_imgs(forward_msg, client))
        return imgs

    async with httpx.AsyncClient() as client:
        # 1. 优先检查引用回复
        if event.reply:
            image_list.extend(await _get_imgs(event.reply.message, client))
        
        # 2. 检查当前消息（包括转发消息）
        image_list.extend(await _get_imgs(event.message, client))
    
    return image_list

def unwrap_images(event: MessageEvent):
    '''
    从消息事件中解包 Image Segment。
    优先检查回复的消息(Reply)，然后检查当前消息。
    '''
    # 1. 检查是否有引用回复 (Reply)
    if event.reply:
        if DEBUG:
            logger.debug('[unwrap_images]: Found Reply')
        # event.reply.message 是一个 Message 对象
        for seg in event.reply.message:
            if seg.type == 'image':
                yield seg

    # 2. 检查当前消息链
    if DEBUG:
        logger.debug('[unwrap_images]: %s', event.message)
    
    for seg in event.message:
        if seg.type == 'image':
            yield seg

async def judge_type(img: MessageSegment | bytes) -> tuple[str, bytes]:
    '''
    判断图片类型，返回 (type, bytes)
    
    Args:
        img: 可以是 OneBot V11 的图片 MessageSegment，或者是已经下载好的 bytes
    '''
    imagebytes = b""

    # 情况1: 输入是 MessageSegment (OneBot V11 图片)
    if isinstance(img, MessageSegment):
        if img.type != 'image':
            raise ValueError("传入的 Segment 不是图片类型")
            
        url = img.data.get("url")
        if not url:
            raise ValueError("无法获取图片 URL")

        # 使用 httpx 异步下载图片
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=20)
            resp.raise_for_status()
            imagebytes = resp.content
            
    # 情况2: 输入已经是 bytes
    elif isinstance(img, bytes):
        imagebytes = img
    else:
        raise TypeError(f"不支持的输入类型: {type(img)}")

    # 使用 PIL 判断格式
    try:
        with PILImage.open(io.BytesIO(imagebytes)) as im:
            # 获取格式 (如 JPEG, PNG, GIF)
            imagetype = im.format.lower()
            # 统一 jpeg 为 jpg
            if imagetype == 'jpeg':
                imagetype = 'jpg'
            return imagetype, imagebytes
    except Exception as e:
        if DEBUG:
            logger.debug("[judge_type] Error: %s", e)
        # 如果 PIL 无法识别，默认回退到 jpg 或抛出异常，视需求而定
        return "jpg", imagebytes

def guess_extension(data: bytes) -> str:
    """简单判断图片类型，返回不带点的后缀名 (如 jpg, png)"""
    ext = imghdr.what(None, data)
    if ext == 'jpeg':
        return 'jpg'
    if ext:
        return ext
    return 'jpg' # 默认回退
