from __future__ import annotations

from argparse import Namespace

from nonebot import on_shell_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import ShellCommandArgs
from nonebot.plugin import PluginMetadata
from nonebot.rule import ArgumentParser

from kanamibot.core import ModuleRule

from .tts_queue import tts_manager

MODULE_NAME = "tts"

__plugin_meta__ = PluginMetadata(
    name="GPT-SoVITS TTS",
    description="使用 GPT-SoVITS 生成语音。",
    usage="#tts [-e 情绪] <文本>",
)

parser = ArgumentParser(description="GPT-SoVITS TTS 命令")
parser.add_argument("-e", "--emotion", dest="emotion", default=None, help="指定情绪")
parser.add_argument("text", nargs="*", help="要转换的文本内容")

tts_handler = on_shell_command(
    "#tts",
    parser=parser,
    priority=5,
    block=True,
    rule=ModuleRule(MODULE_NAME),
)

EMOTION_MAP = {
    "自然": "natural",
    "平静": "natural",
    "默认": "natural",
    "natural": "natural",
    "激动": "excited",
    "兴奋": "excited",
    "开心": "excited",
    "急切": "excited",
    "excited": "excited",
    "沮丧": "frustrate",
    "悲伤": "frustrate",
    "祈求": "frustrate",
    "难过": "frustrate",
    "frustrate": "frustrate",
}


@tts_handler.handle()
async def handle_tts(
    bot: Bot,
    event: MessageEvent,
    args: Namespace = ShellCommandArgs(),  # noqa: B008
) -> None:
    text = " ".join(args.text).strip()
    if not text:
        await tts_handler.finish("请输入要转换的内容。\n格式：#tts [-e 情绪] 内容")

    final_emotion = "natural"
    if args.emotion:
        emotion_input = str(args.emotion).strip()
        final_emotion = EMOTION_MAP.get(emotion_input.lower()) or EMOTION_MAP.get(emotion_input, "")
        if not final_emotion:
            allowed = " ".join(["自然(natural)", "激动(excited)", "沮丧(frustrate)"])
            await tts_handler.finish(f"未知的情绪参数，仅允许：\n{allowed}")

    position = await tts_manager.add_task(bot, event, text, emotion=final_emotion)
    if position > 0:
        await tts_handler.send(f"TTS 任务已加入队列，前方还有 {position} 个任务。")
