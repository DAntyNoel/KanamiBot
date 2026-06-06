from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .group_manager import ADMIN_PERMISSION, __all_modules__, group_config

__plugin_meta__ = PluginMetadata(
    name="KanamiCore",
    description="KanamiBot 核心管理命令与共享能力入口。",
    usage="enable/disable/list module/ban user",
)


cmd_enable = on_command("enable", permission=ADMIN_PERMISSION, priority=1, block=True)


@cmd_enable.handle()
async def handle_enable(event: GroupMessageEvent, args: Message = CommandArg()) -> None:  # noqa: B008
    module_name = args.extract_plain_text().strip()
    if not module_name:
        await cmd_enable.finish("请输入要启用的模块名")

    group_config.set_module_state(str(event.group_id), module_name, True)
    await cmd_enable.finish(f"模块 {module_name} 已在本群启用。")


cmd_disable = on_command("disable", permission=ADMIN_PERMISSION, priority=1, block=True)


@cmd_disable.handle()
async def handle_disable(event: GroupMessageEvent, args: Message = CommandArg()) -> None:  # noqa: B008
    module_name = args.extract_plain_text().strip()
    if not module_name:
        await cmd_disable.finish("请输入要禁用的模块名")

    group_config.set_module_state(str(event.group_id), module_name, False)
    await cmd_disable.finish(f"模块 {module_name} 已在本群禁用。")


cmd_ban = on_command("ban user", permission=SUPERUSER, priority=1, block=True)


@cmd_ban.handle()
async def handle_ban(event: GroupMessageEvent, args: Message = CommandArg()) -> None:  # noqa: B008
    try:
        target_uid = int(args.extract_plain_text().strip())
    except ValueError:
        await cmd_ban.finish("请输入有效的QQ号")
        return

    group_config.ban_user(str(event.group_id), target_uid)
    await cmd_ban.finish(f"用户 {target_uid} 已在本群被拉黑。")


cmd_list_modules = on_command("list module", permission=ADMIN_PERMISSION, priority=1, block=True)


@cmd_list_modules.handle()
async def handle_list_modules(event: GroupMessageEvent) -> None:
    group_id = str(event.group_id)
    group_data = group_config.get_group_data(group_id)
    modules = group_data.get("modules", {})

    def status_icon(state: bool) -> str:
        return "✅" if state else "❌"

    module_status = "\n".join(
        f"{name}: {status_icon(modules.get(name, True))}" for name in sorted(__all_modules__)
    )
    if not module_status:
        module_status = "暂无已注册模块，所有模块默认启用。"

    await cmd_list_modules.finish(f"本群模块状态：\n{module_status}")
