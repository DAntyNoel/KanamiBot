from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import GroupMessageCreateEvent

from .image import __plugin_meta__ as __plugin_meta__


async def _group_only(event: Event) -> bool:
    return isinstance(event, GroupMessageCreateEvent)


gallery_identity = on_command("图库身份", rule=_group_only, priority=10, block=True)


@gallery_identity.handle()
async def show_identity(bot: Bot, event: GroupMessageCreateEvent) -> None:
    await bot.send(event, f"群 OpenID：{event.group_openid}\n用户 OpenID：{event.get_user_id()}\n"
                   "请管理员在本地 qqbot_gallery.json 中绑定群号和 QQ 号；不要发送 AppSecret。")
