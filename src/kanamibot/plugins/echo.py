from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Echo",
    description="Echo text after the command.",
    usage="/echo <text> or echo <text>",
)

echo = on_command("echo", priority=5, block=True)


@echo.handle()
async def handle_echo(args: Message = CommandArg()) -> None:  # noqa: B008
    text = args.extract_plain_text().strip()
    if text:
        await echo.finish(text)
    await echo.finish("Please provide text to echo.")
