from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger


def _mp3_to_silk_bytes(mp3_path: str | Path, rate: int = 24000) -> bytes:
    """
    将 MP3 文件转换为 Silk 编码的 bytes 数据。
    保留了原代码中 24000 的采样率设置，适合高音质语音。
    
    Args:
        mp3_path: MP3 文件路径
        rate: 采样率，默认 24000
    
    Returns:
        bytes: Silk 格式的二进制数据
    """
    try:
        import rsilk
        from pydub import AudioSegment

        path_str = str(mp3_path)
        if not os.path.exists(path_str):
            raise FileNotFoundError(f"Audio file not found: {path_str}")

        # 1. 使用 pydub 读取 MP3 并转换为 PCM
        # pydub 需要系统安装 ffmpeg
        audio = AudioSegment.from_file(path_str, format="mp3")
        
        # 2. 重采样设置 (OneBot/QQ 常用 24000 或 16000)
        audio = audio.set_frame_rate(rate)
        audio = audio.set_channels(1) # 单声道
        audio = audio.set_sample_width(2) # 16bit
        
        pcm_data = audio.raw_data
        
        # 3. 使用 rsilk 编码 PCM -> Silk
        silk_data = rsilk.encode(pcm_data, rate, rate)
        
        return silk_data
        
    except Exception as exc:
        logger.error(f"Audio conversion failed: {exc}")
        raise


def mp3_to_segment(file_path: str | Path, use_transcode: bool = True) -> MessageSegment:
    """
    获取语音消息段 (MessageSegment)。
    
    Args:
        file_path: MP3 文件路径
        use_transcode: 是否在 Python 侧进行转码 (MP3->Silk)。
                       如果为 True，使用 pydub+rsilk 转换（兼容性好，但慢）。
                       如果为 False，直接发送 MP3 文件给 OneBot 客户端
                       （依赖客户端如 Go-CQHTTP 内置的 ffmpeg）。
    
    Returns:
        MessageSegment: 可直接发送的语音消息段
    """
    if use_transcode:
        # 方案 A: Python 端转码 (原代码逻辑)
        # 优点: 只要 Python 能跑，发出去的一定是 Silk，兼容所有 OneBot 实现
        try:
            silk_data = _mp3_to_silk_bytes(file_path)
            return MessageSegment.record(BytesIO(silk_data))
        except Exception as e:
            logger.warning(f"本地转码失败，尝试直接发送原文件: {e}")
            return MessageSegment.record(file=file_path)
    else:
        # 方案 B: 直接发送文件路径
        # 优点: 速度快，无需 Python 依赖
        # 缺点: 依赖 Bot 客户端 (如 go-cqhttp) 配置好了 ffmpeg
        return MessageSegment.record(file=file_path)
