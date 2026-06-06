"""
视频处理工具模块
提供视频到GIF的转换功能
"""
from __future__ import annotations

import os
from pathlib import Path

import imageio
from PIL import Image


def mp4_to_gif(
    input_path: str,
    output_path: str | None = None,
    fps: int = 10,
    scale: tuple[int, int] | None = None,
    start_time: float | None = None,
    duration: float | None = None,
    optimize: bool = True,
    quality: int = 85,
) -> str:
    """
    将MP4视频转换为GIF动图
    
    Args:
        input_path: 输入的MP4文件路径
        output_path: 输出的GIF文件路径，如果为None则自动生成
        fps: 输出GIF的帧率，默认10帧/秒
        scale: 缩放尺寸(width, height)，None表示保持原始尺寸
        start_time: 开始时间（秒），None表示从头开始
        duration: 持续时间（秒），None表示到结尾
        optimize: 是否优化GIF大小
        quality: 质量参数(1-100)，越高质量越好但文件越大
        
    Returns:
        str: 输出的GIF文件路径
        
    Raises:
        FileNotFoundError: 输入文件不存在
        ValueError: 参数不合法
    """
    # 检查输入文件
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    
    # 生成输出路径
    if output_path is None:
        input_file = Path(input_path)
        output_path = str(input_file.with_suffix('.gif'))
    
    # 验证参数
    if fps <= 0:
        raise ValueError("fps必须大于0")
    if quality < 1 or quality > 100:
        raise ValueError("quality必须在1-100之间")
    
    try:
        # 读取视频
        reader = imageio.get_reader(input_path)
        meta = reader.get_meta_data()
        
        # 计算原始视频的fps
        original_fps = meta.get('fps', 30)
        
        # 计算帧跳过间隔（降低帧率）
        frame_skip = max(1, int(original_fps / fps))
        
        # 计算起始和结束帧
        total_frames = reader.count_frames()
        start_frame = int(start_time * original_fps) if start_time is not None else 0
        end_frame = total_frames
        if duration is not None:
            end_frame = min(start_frame + int(duration * original_fps), total_frames)
        
        # 提取并处理帧
        frames = []
        for i, frame in enumerate(reader):
            # 跳过指定范围外的帧
            if i < start_frame:
                continue
            if i >= end_frame:
                break
            
            # 按帧率跳过
            if (i - start_frame) % frame_skip != 0:
                continue
            
            # 转换为PIL Image
            img = Image.fromarray(frame)
            
            # 缩放
            if scale is not None:
                img = img.resize(scale, Image.Resampling.LANCZOS)
            
            # 转换为RGB（GIF需要）
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            frames.append(img)
        
        reader.close()
        
        if not frames:
            raise ValueError("没有提取到任何帧")
        
        # 保存为GIF
        duration_ms = int(1000 / fps)  # 每帧持续时间（毫秒）
        
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=optimize,
            quality=quality
        )
        
        return output_path
        
    except Exception as e:
        raise RuntimeError(f"转换过程中出错: {e}") from e


def mp4_to_gif_simple(input_path: str, output_path: str | None = None) -> str:
    """
    简化版MP4转GIF，使用默认参数
    
    Args:
        input_path: 输入的MP4文件路径
        output_path: 输出的GIF文件路径
        
    Returns:
        str: 输出的GIF文件路径
    """
    return mp4_to_gif(
        input_path=input_path,
        output_path=output_path,
        fps=10,
        scale=None,
        optimize=True
    )


def get_video_info(video_path: str) -> dict:
    """
    获取视频文件的基本信息
    
    Args:
        video_path: 视频文件路径
        
    Returns:
        dict: 包含视频信息的字典
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")
    
    try:
        reader = imageio.get_reader(video_path)
        meta = reader.get_meta_data()
        frame_count = reader.count_frames()
        reader.close()
        
        return {
            'fps': meta.get('fps', 30),
            'duration': meta.get('duration', 0),
            'size': meta.get('size', (0, 0)),
            'frame_count': frame_count
        }
    except Exception as e:
        raise RuntimeError(f"读取视频信息失败: {e}") from e


# 示例用法
if __name__ == "__main__":
    # 基本用法
    # mp4_to_gif_simple("input.mp4", "output.gif")
    
    # 高级用法
    # mp4_to_gif(
    #     input_path="input.mp4",
    #     output_path="output.gif",
    #     fps=15,
    #     scale=(480, 320),
    #     start_time=2.0,
    #     duration=5.0,
    #     optimize=True,
    #     quality=90
    # )
    pass
