from nonebot import on_command
from nonebot.internal.matcher import Matcher
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Ping",
    description="Connection test command for NapCat / OneBot v11.",
    usage="/ping or ping",
)

ping = on_command("ping", priority=5, block=True)


@ping.handle()
async def handle_ping(matcher: Matcher) -> None:
    await matcher.finish("pong")
