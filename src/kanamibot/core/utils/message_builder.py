from __future__ import annotations

import os
import random
import tempfile
from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

# --- 配置区域 ---
# 图片大小阈值 (单位: 字节)
# 建议设为 100KB - 500KB。QQ 协议对 Base64 的限制通常在 1MB 左右，但为了保险起见，
# 超过 200KB (200 * 1024) 就转存为临时文件发送是最稳妥的。
IMG_SIZE_THRESHOLD = int(os.getenv('CORE_IMG_SIZE_THRESHOLD', 200 * 1024))
# ----------------


async def messagechain_builder(
    reply_choices: list[str] | None = None,
    text: str | None = None,
    imgpath: str | Path | bytes | BytesIO | None = None,
    rndimg: bool = False,
    imgurl: str | None = None,
    imgbase64: str | bytes | BytesIO | None = None,
    imgseg: MessageSegment | None = None,
    at: list[int] | int | None = None,
    atall: bool = False,
) -> Message:
    """
    构造 NoneBot 消息链 (已优化：支持 Path 对象发送本地文件)
    """
    msg = Message()

    # 1. 处理 At
    if at:
        if isinstance(at, int):
            msg += MessageSegment.at(at) + MessageSegment.text(" ")
        else:
            for _at in at:
                msg += MessageSegment.at(_at) + MessageSegment.text(" ")
    elif atall:
        msg += MessageSegment.at("all") + MessageSegment.text(" ")

    # 2. 处理文字回复
    has_text = False
    if reply_choices:
        msg += MessageSegment.text(random.choice(reply_choices))
        has_text = True
    elif text:
        msg += MessageSegment.text(text)
        has_text = True
    
    if has_text:
        msg += MessageSegment.text("\n")

    # 3. 统一图片处理逻辑
    # 我们定义一个变量 target_image 来承载最终要发送的图片对象
    target_image = None
    if imgseg:
        msg += imgseg
        return msg

    if imgpath:
        target_image = imgpath
    elif imgurl:
        target_image = imgurl
    elif imgbase64:
        target_image = imgbase64

    if rndimg and reply_choices:
        logger.debug("messagechain_builder received rndimg=True; no image pool is configured.")

    # 开始构建图片消息
    if target_image:
        img_data = None
        # 提取二进制数据
        if isinstance(target_image, BytesIO):
            img_data = target_image.getvalue()
        elif isinstance(target_image, bytes):
            img_data = target_image
        if img_data and len(img_data) > IMG_SIZE_THRESHOLD:
            try:
                # 创建临时文件 (delete=False 确保关闭后文件还在，等待 Bot 读取)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    f.write(img_data)
                    target_image = Path(f.name) 
                    logger.info(
                        "[MsgBuilder] 图片大小 (%s bytes) 超过阈值，已自动转存为临时文件: %s",
                        len(img_data),
                        f.name,
                    )
            except OSError as exc:
                logger.warning("[MsgBuilder] 转存临时文件失败，将尝试原样发送: %s", exc)

        # 情况 A: 如果是 Path 对象 (这是我们现在主要用的方式)
        if isinstance(target_image, Path):
            msg += MessageSegment.image(target_image)
        
        # 情况 B: 如果是本地文件路径字符串
        elif isinstance(target_image, str) and os.path.exists(target_image):
            msg += MessageSegment.image(Path(target_image))
            
        # 情况 C: 如果是网络 URL
        elif isinstance(target_image, str) and target_image.startswith("http"):
            msg += MessageSegment.image(target_image)
            
        # 情况 D: 如果是 Bytes (内存二进制流)
        elif isinstance(target_image, (bytes, BytesIO)):
            msg += MessageSegment.image(target_image)
            
        # 兼容旧逻辑：如果是不带协议头的 Base64 字符串 (容易出问题，尽量避免进入此分支)
        elif isinstance(target_image, str):
            if target_image.startswith("base64://"):
                msg += MessageSegment.image(target_image)
            else:
                # 尝试补全协议头 (仅做最后挣扎)
                msg += MessageSegment.image(f"base64://{target_image}")
                
    return msg

def build_forward_message(
    msgs: list[Message],
    user_id: str = "2407303621", 
    nickname: str = 'Kanami'
) -> list[MessageSegment]:
    """
    构建一个转发消息链 (OneBot V11)
    """
    nodes = []
    for msg in msgs:
        # 构建自定义转发节点
        node = MessageSegment.node_custom(
            user_id=user_id,
            nickname=nickname,
            content=msg
        )
        nodes.append(node)
    return nodes

def msg_to_forward(
    msg: Message | str | MessageSegment,
    user_id: str = "2407303621", 
    nickname: str = 'Kanami'
) -> list[MessageSegment]:
    """
    将一个 Message 对象中的所有 Segment 拆分，每个 Segment 转化为一个独立的转发节点。
    
    参数:
        msg: 包含多个段的消息对象 (如果是字符串会自动转为单段 Message)
        user_id: 转发节点显示的 QQ 号
        nickname: 转发节点显示的昵称
        
    返回:
        List[MessageSegment]: 拆分后的节点列表
    """
    
    # 统一转化为 Message 对象，方便遍历
    if isinstance(msg, str):
        msg = Message(msg)
    elif isinstance(msg, MessageSegment):
        msg = Message([msg])

    forward_nodes = []

    for seg in msg:
        # 过滤掉一些不适合单独成条的消息段（如空文本或特定的元数据）
        if seg.type == "text" and not seg.data.get("text", "").strip():
            continue
            
        # 为每一个 segment 创建一个独立的 node
        node = MessageSegment.node_custom(
            user_id=user_id,
            nickname=nickname,
            content=Message(seg) # 包装成独立的 Message
        )
        forward_nodes.append(node)
    
    return forward_nodes
