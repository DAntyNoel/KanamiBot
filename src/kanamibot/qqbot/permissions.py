from __future__ import annotations

import asyncio
from collections import OrderedDict

from nonebot import on_command, on_notice
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import GroupAddRobotEvent, GroupMessageCreateEvent
from nonebot.exception import ActionFailed, NetworkError
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="QQBot 群权限提醒",
    description="进群时提醒群主开启全量消息接收和主动消息推送。",
    usage="@机器人 权限设置（也支持 群权限、机器人权限）",
    supported_adapters={"~qq"},
)

PERMISSION_GUIDE = (
    "请群主为本群开启以下 QQ 官方机器人功能：\n"
    "1. 接收所有消息：开启后可直接发送 ping 等指令，无需每次 @机器人。\n"
    "2. 允许主动消息／主动推送：开启后，已接入的定时提醒、更新通知才能主动发送。\n"
    "请使用最新版手机 QQ，从本群的机器人管理或机器人资料页进入权限设置，"
    "找到上述开关并开启（名称以客户端显示为准）。\n"
    "这是设置指引，不代表已检测到权限关闭；开启权限也不会自动订阅通知。\n"
    "可直接发送 ping 测试免艾特；未开启前请发送 @机器人 ping。\n"
    "需要再次查看时，发送 @机器人 权限设置。"
)

_sent_events: OrderedDict[tuple[str, str, str], None] = OrderedDict()
_notice_lock = asyncio.Lock()


async def is_group_join(event: Event) -> bool:
    return isinstance(event, GroupAddRobotEvent)


async def is_qq_group_message(event: Event) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


join_reminder = on_notice(rule=is_group_join, priority=10, block=False)
permission_help = on_command(
    "权限设置",
    aliases={"群权限", "机器人权限"},
    rule=is_qq_group_message,
    priority=5,
    block=True,
)


@join_reminder.handle()
async def remind_group_owner(bot: Bot, event: GroupAddRobotEvent) -> None:
    if not event.event_id:
        logger.warning("QQBot 进群事件缺少 event_id，跳过权限提醒；可在群内发送 权限设置")
        return
    event_key = (bot.self_id, event.group_openid, event.event_id)
    async with _notice_lock:
        if event_key in _sent_events:
            return
        try:
            await bot.send_to_group(
                group_openid=event.group_openid,
                message=PERMISSION_GUIDE,
                event_id=event.event_id,
            )
        except (ActionFailed, NetworkError):
            logger.warning("QQBot 进群权限提醒发送失败；可在群内发送 @机器人 权限设置")
            return
        _sent_events[event_key] = None
        if len(_sent_events) > 256:
            _sent_events.popitem(last=False)


@permission_help.handle()
async def show_permission_guide(bot: Bot, event: GroupMessageCreateEvent) -> None:
    await bot.send(event, PERMISSION_GUIDE)
