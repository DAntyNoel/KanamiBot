"""Keep Skland's acknowledgement and completion reactions consistent with KanamiBot."""

from nonebot_plugin_alconna import message_reaction
from nonebot_plugin_skland.utils import message as upstream_message

from kanamibot.core.message_reactions import OK_EMOJI_ID

_OK_REACTIONS = {"66": OK_EMOJI_ID, "144": OK_EMOJI_ID, "❤": "👌", "🎉": "👌"}


async def _message_reaction(emoji: str) -> None:
    await message_reaction(_OK_REACTIONS.get(emoji, emoji))


def install_ok_reactions() -> None:
    # Command modules already imported send_reaction; replace its module-local
    # sender so those existing references also use OK without changing Alconna.
    upstream_message.message_reaction = _message_reaction
