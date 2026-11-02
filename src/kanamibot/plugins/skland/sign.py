"""Use the shared sign result policy for interactive commands as well as daily jobs."""

from datetime import datetime

from nonebot_plugin_alconna import Arparma, UniMessage
from nonebot_plugin_orm import async_scoped_session
from nonebot_plugin_skland import commands
from nonebot_plugin_skland.commands import arksign, endfield
from nonebot_plugin_skland.commands.endfield import sign as efsign
from nonebot_plugin_skland.schemas import SignGame
from nonebot_plugin_skland.services import sign as signing
from nonebot_plugin_user import UserSession


async def _sign_personal(
    user_session: UserSession,
    session: async_scoped_session,
    role_index: int | None,
    result: Arparma,
    game: SignGame,
) -> None:
    command = arksign if game == "arknights" else efsign
    characters = await command._select_characters(user_session, session, role_index, result)
    if not characters:
        return
    command.send_reaction(user_session, "processing")
    entries = [
        await signing.sign_character(character.account, character, game) for character in characters
    ]
    await session.commit()

    # Daily jobs already distinguish an already-signed role from an API failure.
    # Keep that same policy without guessing rewards or retrying the sign request.
    formatter = (
        signing.format_sign_result if game == "arknights" else signing.format_endfield_sign_result
    )
    formatted = formatter(entries, datetime.now().strftime("%Y-%m-%d %H:%M"), True)
    if formatted.success_count:
        command.send_reaction(user_session, "done")
    await UniMessage("\n".join(content for _title, content in formatted.results)).send(
        at_sender=True
    )


async def arksign_sign_handler(
    user_session: UserSession,
    session: async_scoped_session,
    role_index: int | None,
    result: Arparma,
) -> None:
    await _sign_personal(user_session, session, role_index, result, "arknights")


async def ef_sign_handler(
    user_session: UserSession,
    session: async_scoped_session,
    role_index: int | None,
    result: Arparma,
) -> None:
    await _sign_personal(user_session, session, role_index, result, "endfield")


def install_sign_handlers() -> None:
    arksign.arksign_sign_handler = arksign_sign_handler
    efsign.ef_sign_handler = ef_sign_handler
    # The Endfield matcher imports its handler from the package's re-export.
    endfield.ef_sign_handler = ef_sign_handler
    commands.arksign_sign_handler = arksign_sign_handler
    commands.ef_sign_handler = ef_sign_handler
