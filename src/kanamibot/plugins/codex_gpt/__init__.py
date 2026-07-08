from __future__ import annotations

import asyncio
import base64
import imghdr
import os
import random
import re
import time
from collections import deque

import httpx
from nonebot import get_driver, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.message import event_preprocessor
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from kanamibot.core.chat_history import load_recent_group_history
from kanamibot.core.group_manager import ModuleRule, group_config
from kanamibot.core.media_storage import AdvancedMediaStorageSystem
from kanamibot.core.paths import DATA_DIR
from kanamibot.core.utils.image import download_all_images_from_event

from .client import CodexGPTClient, CodexGPTError, CodexGPTImageTextResponse
from .config import load_config
from .diagnostics import CodexGPTDebugLogger
from .session import SessionStore

__plugin_meta__ = PluginMetadata(
    name="Codex GPT",
    description="OpenAI-compatible 群聊/私聊对话与图片生成",
    usage="#gpt <问题>\n#image <图片描述>",
)


CMD_PATTERN = re.compile(
    r"^(@[0-9]+)?\s*(#gpt|#codex|#chat)\s*(.*)$", flags=re.IGNORECASE | re.DOTALL
)
IMAGE_CMD_PATTERN = re.compile(
    r"^(@[0-9]+)?\s*(#image|#img)\s*(.*)$", flags=re.IGNORECASE | re.DOTALL
)
HELP_ALIASES = {"help", "帮助", "-h", "--help", "？", "?"}
CLEAR_ALIASES = {"clear", "reset", "new", "清空", "重置", "新建"}
STATUS_ALIASES = {"status", "history", "config", "状态", "上下文", "配置"}
FORGET_ALIASES = {"forget", "undo", "撤回", "忘记"}

config = load_config()
debug = CodexGPTDebugLogger(config)
debug.log(
    "plugin.loaded",
    configured=config.is_configured,
    base_url=config.base_url,
    default_model=config.default_model,
    image_model=config.image_model,
    debug=config.debug,
    debug_log_file=str(config.debug_log_file),
    image_size=config.image_size,
    image_timeout_seconds=config.image_timeout_seconds,
    stream=config.stream,
    active_reply=config.active_reply,
    active_reply_probability=config.active_reply_probability,
    active_reply_at_probability=config.active_reply_at_probability,
    active_reply_groups=config.active_reply_groups,
    active_reply_rate_window_minutes=config.active_reply_rate_window_minutes,
    active_reply_rate_limit=config.active_reply_rate_limit,
    active_reply_history_messages=config.active_reply_history_messages,
    active_reply_memory_messages=config.active_reply_memory_messages,
    active_reply_max_prompt_chars=config.active_reply_max_prompt_chars,
)
if config.active_reply and config.is_configured:
    debug.log("active.listener_enabled", hook="event_preprocessor", background=True)
else:
    debug.log(
        "active.listener_disabled",
        configured=config.is_configured,
        active_reply_env=os.getenv("CODEX_GPT_ACTIVE_REPLY"),
        probability_env=os.getenv("CODEX_GPT_ACTIVE_REPLY_PROBABILITY"),
        at_probability_env=os.getenv("CODEX_GPT_ACTIVE_REPLY_AT_PROBABILITY"),
        groups_env=os.getenv("CODEX_GPT_ACTIVE_REPLY_GROUPS"),
        hint="set CODEX_GPT_ACTIVE_REPLY=1 to enable group active reply",
    )
client = CodexGPTClient(config, debug=debug)
store = SessionStore(config)
image_storage = AdvancedMediaStorageSystem(
    "gptimage2",
    data_root=DATA_DIR / "codex_gpt" / "images",
    refresh_global_index=False,
)
_model_cache: list[str] = []
_model_cache_at = 0.0
_MODEL_CACHE_TTL = 300.0
_active_memories: dict[int, deque[tuple[float, int, str, str]]] = {}
_active_locks: dict[int, asyncio.Lock] = {}
_active_reply_times: dict[int, deque[float]] = {}
_active_reply_tasks: set[asyncio.Task[None]] = set()

codex_gpt = on_regex(
    CMD_PATTERN.pattern,
    flags=re.IGNORECASE | re.DOTALL,
    priority=8,
    block=True,
    rule=ModuleRule("codex_gpt"),
)

codex_image = on_regex(
    IMAGE_CMD_PATTERN.pattern,
    flags=re.IGNORECASE | re.DOTALL,
    priority=8,
    block=True,
    rule=ModuleRule("codex_gpt"),
)

if config.active_reply and config.is_configured:

    @event_preprocessor
    async def handle_codex_active_reply(bot: Bot, event: MessageEvent):
        if not isinstance(event, GroupMessageEvent):
            return
        task = asyncio.create_task(_run_active_reply_from_preprocessor(bot, event))
        _active_reply_tasks.add(task)
        task.add_done_callback(_active_reply_tasks.discard)


async def _run_active_reply_from_preprocessor(bot: Bot, event: GroupMessageEvent) -> None:
    try:
        await _handle_active_reply(bot, event)
    except Exception as exc:
        logger.exception("[codex_gpt] active reply preprocessor error")
        debug.exception(
            "active.preprocessor_error",
            group_id=getattr(event, "group_id", None),
            error_type=exc.__class__.__name__,
            error=_safe_error(exc),
        )


@codex_image.handle()
async def handle_codex_image(bot: Bot, event: MessageEvent):
    prompt = _extract_image_payload(event)
    session_id = _session_id(event)
    input_images = await _download_event_images(event, session_id, bot)
    debug.log(
        "image.command_received",
        session_id=session_id,
        user_id=getattr(event, "user_id", None),
        group_id=getattr(event, "group_id", None),
        chars=len(prompt),
        image_count=len(input_images),
        tail=_shorten(prompt[-80:], 80),
    )

    if (not prompt and not input_images) or prompt.lower() in HELP_ALIASES:
        debug.log("image.command_help", session_id=session_id)
        await codex_image.finish(_reply(event) + _image_help_text())
        return

    await _run_image(bot, event, session_id, prompt, input_images)


@codex_gpt.handle()
async def handle_codex_gpt(bot: Bot, event: MessageEvent):
    raw_text = _extract_payload(event)
    session_id = _session_id(event)
    input_images = await _download_event_images(event, session_id, bot)
    debug.log(
        "command.received",
        session_id=session_id,
        user_id=getattr(event, "user_id", None),
        group_id=getattr(event, "group_id", None),
        chars=len(raw_text),
        image_count=len(input_images),
        tail=_shorten(raw_text[-80:], 80),
    )

    if (not raw_text and not input_images) or raw_text.lower() in HELP_ALIASES:
        debug.log("command.help", session_id=session_id)
        await codex_gpt.finish(_reply(event) + _help_text())
        return

    command, rest = _split_first(raw_text)
    command_lower = command.lower()

    if raw_text.lower() in CLEAR_ALIASES:
        debug.log("session.clear", session_id=session_id)
        await store.clear(session_id)
        await codex_gpt.finish(_reply(event) + "已新建对话，上下文清空。")
        return

    if raw_text.lower() in STATUS_ALIASES:
        debug.log("session.status", session_id=session_id)
        await codex_gpt.finish(_reply(event) + await _status_text(session_id))
        return

    if raw_text.lower() in FORGET_ALIASES:
        removed = await store.forget_last_turn(session_id)
        debug.log("session.forget", session_id=session_id, removed=removed)
        text = "已忘记上一轮对话。" if removed else "当前没有可忘记的上下文。"
        await codex_gpt.finish(_reply(event) + text)
        return

    if command_lower in {"model", "模型"}:
        await _handle_model_command(bot, session_id, rest, event)
        return

    if command_lower in {"models", "模型列表"}:
        await _handle_models_command(event)
        return

    if command_lower in {"system", "prompt", "设定"}:
        await _handle_system_command(session_id, rest, event)
        return

    if command_lower in {"one", "once", "单次"} and rest:
        await _run_chat(bot, event, session_id, rest, stateless=True, input_images=input_images)
        return

    await _run_chat(bot, event, session_id, raw_text, stateless=False, input_images=input_images)


async def _run_chat(
    bot: Bot,
    event: MessageEvent,
    session_id: str,
    prompt: str,
    stateless: bool,
    input_images: list[bytes] | None = None,
) -> None:
    prompt = prompt.strip()
    image_count = len(input_images or [])
    debug.log(
        "chat.start",
        session_id=session_id,
        stateless=stateless,
        prompt_chars=len(prompt),
        image_count=image_count,
        prompt_tail=_shorten(prompt[-80:], 80),
    )
    if not prompt and not image_count:
        debug.log("chat.empty_prompt", session_id=session_id)
        await codex_gpt.finish(_reply(event) + _help_text())
        return
    if not config.is_configured:
        debug.log("chat.not_configured", session_id=session_id)
        await codex_gpt.finish(_reply(event) + _not_configured_text())
        return

    status_message_id = None
    try:
        status_ret = await codex_gpt.send(
            _reply(event) + ("单次思考中..." if stateless else "思考中...")
        )
        if isinstance(status_ret, dict):
            status_message_id = status_ret.get("message_id")
    except Exception as exc:
        logger.warning(f"[codex_gpt] failed to send status message: {exc}")
        debug.log("chat.status_message_failed", session_id=session_id, error=_safe_error(exc))

    try:
        session = await store.get(session_id)
        messages = await store.build_messages(session_id, prompt, stateless=stateless)
        model = session.model or config.default_model
        is_image_model = _is_image_model(model)
        if not prompt and image_count:
            prompt = "请基于图片内容进行创作。" if is_image_model else "请描述这张图片。"
            messages = await store.build_messages(session_id, prompt, stateless=stateless)
        debug.log(
            "chat.context_ready",
            session_id=session_id,
            model=model,
            message_count=len(messages),
            history_count=max(0, len(messages) - 2),
            image_count=image_count,
            image_model=is_image_model,
        )
        if is_image_model:
            debug.log("chat.image_model_rejected", session_id=session_id, model=model)
            raise CodexGPTError("图片生成请使用 #image <描述>，#gpt 只用于对话。")
        else:
            if input_images:
                messages = _attach_images_to_latest_user_message(messages, input_images)
            answer = await client.chat(
                messages=messages,
                model=model,
                temperature=config.temperature,
            )
        if not stateless:
            await store.add_turn(session_id, prompt, answer)

        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        await _send_long_reply(event, answer)
        debug.log(
            "chat.success", session_id=session_id, answer_chars=len(answer), stateless=stateless
        )
    except (CodexGPTError, httpx.HTTPError) as exc:
        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        debug.log(
            "chat.error",
            session_id=session_id,
            error_type=exc.__class__.__name__,
            error=_safe_error(exc),
        )
        await codex_gpt.send(_reply(event) + f"请求失败：{_safe_error(exc)}")
    except Exception as exc:
        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        logger.exception("[codex_gpt] unexpected error")
        debug.exception(
            "chat.unexpected_error",
            session_id=session_id,
            error_type=exc.__class__.__name__,
            error=_safe_error(exc),
        )
        await codex_gpt.send(_reply(event) + f"发生未知错误：{_safe_error(exc)}")


async def _run_image(
    bot: Bot,
    event: MessageEvent,
    session_id: str,
    prompt: str,
    input_images: list[bytes] | None = None,
) -> None:
    prompt = prompt.strip()
    input_images = input_images or []
    if not prompt and input_images:
        prompt = "请基于图片内容进行创作。"
    if not config.is_configured:
        debug.log("image.not_configured", session_id=session_id)
        await codex_image.finish(_reply(event) + _not_configured_text())
        return

    status_message_id = None
    try:
        status_text = "图片编辑中..." if input_images else "图片生成中..."
        status_ret = await codex_image.send(_reply(event) + status_text)
        if isinstance(status_ret, dict):
            status_message_id = status_ret.get("message_id")
    except Exception as exc:
        logger.warning(f"[codex_gpt] failed to send image status message: {exc}")
        debug.log("image.status_message_failed", session_id=session_id, error=_safe_error(exc))

    try:
        input_records = _store_codex_image_assets(
            source="qq",
            images=input_images,
            session_id=session_id,
            prompt=prompt,
            event=event,
            verify_hash_collision=True,
        )
        if input_records:
            debug.log(
                "image.inputs_stored",
                session_id=session_id,
                count=len(input_records),
                statuses=[record.get("status") for record in input_records],
            )
        debug.log(
            "image.start",
            session_id=session_id,
            model=config.image_model,
            prompt_chars=len(prompt),
            image_count=len(input_images),
        )
        image = await client.create_image(
            prompt=prompt, model=config.image_model, images=input_images
        )

        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        output_record = _store_codex_image_asset(
            source="openai",
            image_data=image.data,
            session_id=session_id,
            prompt=prompt,
            event=event,
            model=config.image_model,
            mime_type=image.mime_type,
            revised_prompt=image.revised_prompt,
            text=image.text,
        )
        await codex_image.send(_reply(event) + MessageSegment.image(image.data))
        if image.text:
            await _send_long_reply(event, image.text, bot=bot)
        debug.log(
            "image.success",
            session_id=session_id,
            model=config.image_model,
            bytes=len(image.data),
            text_chars=len(image.text or ""),
            stored_file_id=output_record.get("file_id") if output_record else None,
            stored_status=output_record.get("status") if output_record else None,
        )
    except CodexGPTImageTextResponse as exc:
        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        text = exc.text.strip()
        debug.log(
            "image.text_response",
            session_id=session_id,
            model=config.image_model,
            chars=len(text),
        )
        await _send_long_reply(event, text, bot=bot)
    except (CodexGPTError, httpx.HTTPError) as exc:
        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        debug.log(
            "image.error",
            session_id=session_id,
            error_type=exc.__class__.__name__,
            error=_safe_error(exc),
        )
        await codex_image.send(_reply(event) + f"图片请求失败：{_safe_error(exc)}")
    except Exception as exc:
        if status_message_id is not None:
            await _delete_message(bot, status_message_id)
        logger.exception("[codex_gpt] unexpected image error")
        debug.exception(
            "image.unexpected_error",
            session_id=session_id,
            error_type=exc.__class__.__name__,
            error=_safe_error(exc),
        )
        await codex_image.send(_reply(event) + f"发生未知错误：{_safe_error(exc)}")


async def _handle_active_reply(bot: Bot, event: GroupMessageEvent) -> None:
    group_id = int(event.group_id)
    user_id = int(event.user_id)
    debug.log(
        "active.event_received",
        group_id=group_id,
        user_id=user_id,
        message_id=getattr(event, "message_id", None),
    )

    if str(user_id) == str(bot.self_id):
        debug.log("active.skip_self", group_id=group_id, user_id=user_id)
        return

    module_allowed, module_reason = _active_module_allowed(event)
    if not module_allowed:
        debug.log(
            "active.skip_module_rule", group_id=group_id, user_id=user_id, reason=module_reason
        )
        return

    if config.active_reply_groups and group_id not in config.active_reply_groups:
        debug.log(
            "active.skip_group_not_allowed",
            group_id=group_id,
            allowed_groups=config.active_reply_groups,
        )
        return

    at_bot = _is_at_bot(event, bot)
    trigger_type = "at" if at_bot else "ambient"
    text = _active_message_text(event, at_bot=at_bot).strip()
    if not text:
        debug.log("active.skip_empty_message", group_id=group_id, user_id=user_id)
        return
    if _is_codex_command(text):
        debug.log(
            "active.skip_command", group_id=group_id, user_id=user_id, tail=_shorten(text[-80:], 80)
        )
        return

    nickname = _event_nickname(event)
    _remember_active_message(event, text, nickname)
    debug.log(
        "active.message_recorded",
        group_id=group_id,
        user_id=user_id,
        trigger_type=trigger_type,
        at_bot=at_bot,
        chars=len(text),
        memory_count=len(_active_memories.get(group_id, ())),
    )

    probability = config.active_reply_at_probability if at_bot else config.active_reply_probability
    roll = random.random()
    if probability <= 0 or roll >= probability:
        debug.log(
            "active.skip_probability",
            group_id=group_id,
            trigger_type=trigger_type,
            probability=probability,
            roll=roll,
        )
        return
    debug.log(
        "active.probability_hit",
        group_id=group_id,
        trigger_type=trigger_type,
        probability=probability,
        roll=roll,
    )

    allowed, rate_count = _active_rate_allowed(group_id)
    if not allowed:
        debug.log(
            "active.skip_rate_limit",
            group_id=group_id,
            window_minutes=config.active_reply_rate_window_minutes,
            limit=config.active_reply_rate_limit,
            count=rate_count,
        )
        return

    lock = _active_locks.setdefault(group_id, asyncio.Lock())
    if lock.locked():
        debug.log("active.skip_locked", group_id=group_id)
        return

    async with lock:
        allowed, rate_count = _active_rate_allowed(group_id)
        if not allowed:
            debug.log(
                "active.skip_rate_limit_locked",
                group_id=group_id,
                window_minutes=config.active_reply_rate_window_minutes,
                limit=config.active_reply_rate_limit,
                count=rate_count,
            )
            return

        try:
            prompt = await _build_active_prompt(group_id, text, nickname)
            messages = [
                {"role": "system", "content": config.default_system_prompt},
                {"role": "user", "content": prompt},
            ]
            debug.log(
                "active.chat_start",
                group_id=group_id,
                user_id=user_id,
                trigger_type=trigger_type,
                model=config.default_model,
                prompt_chars=len(prompt),
            )
            answer = await client.chat(
                messages=messages,
                model=config.default_model,
                temperature=config.temperature,
            )
            await _send_long_reply(event, answer, bot=bot)
            reply_count = _record_active_reply(group_id)
            debug.log(
                "active.chat_success",
                group_id=group_id,
                answer_chars=len(answer),
                rate_count=reply_count,
                rate_limit=config.active_reply_rate_limit,
                rate_window_minutes=config.active_reply_rate_window_minutes,
            )
        except (CodexGPTError, httpx.HTTPError) as exc:
            debug.log(
                "active.chat_error",
                group_id=group_id,
                error_type=exc.__class__.__name__,
                error=_safe_error(exc),
            )
        except Exception as exc:
            logger.exception("[codex_gpt] unexpected active reply error")
            debug.exception(
                "active.unexpected_error",
                group_id=group_id,
                error_type=exc.__class__.__name__,
                error=_safe_error(exc),
            )


async def _handle_model_command(bot: Bot, session_id: str, rest: str, event: MessageEvent) -> None:
    debug.log("model.command", session_id=session_id, rest=rest)
    if not rest:
        session = await store.get(session_id)
        model = session.model or config.default_model
        debug.log("model.show_help", session_id=session_id, model=model)
        await codex_gpt.finish(_reply(event) + _model_help_text(model))
        return

    subcommand, value = _split_first(rest)
    subcommand_lower = subcommand.lower()

    if subcommand_lower in {"list", "ls", "models", "search", "find", "可用", "列表", "搜索"}:
        keyword = value.strip() if subcommand_lower in {"search", "find", "搜索"} else value.strip()
        debug.log("model.list_command", session_id=session_id, keyword=keyword)
        await _send_model_list(event, keyword)
        return

    if subcommand_lower in {"show", "current", "status", "当前"}:
        session = await store.get(session_id)
        model = session.model or config.default_model
        debug.log("model.show", session_id=session_id, model=model)
        await codex_gpt.finish(_reply(event) + f"当前模型：{model}")
        return

    if not await SUPERUSER(bot, event):
        debug.log(
            "model.permission_denied",
            session_id=session_id,
            user_id=getattr(event, "user_id", None),
            rest=rest,
        )
        await codex_gpt.finish(_reply(event) + "只有 superuser 可以切换 Codex GPT 模型。")
        return

    if rest.lower() in {"default", "reset", "默认", "重置"} or subcommand_lower in {
        "default",
        "reset",
        "默认",
        "重置",
    }:
        debug.log("model.reset", session_id=session_id, default_model=config.default_model)
        await store.set_model(session_id, None)
        await codex_gpt.finish(_reply(event) + f"已恢复默认模型：{config.default_model}")
        return

    model_arg = value if subcommand_lower in {"use", "set", "select", "选择", "切换"} else rest
    try:
        model = await _resolve_model_arg(model_arg.strip())
    except (CodexGPTError, httpx.HTTPError) as exc:
        debug.log(
            "model.resolve_error",
            session_id=session_id,
            model_arg=model_arg,
            error=_safe_error(exc),
        )
        await codex_gpt.finish(_reply(event) + f"切换模型失败：{_safe_error(exc)}")
        return
    if _is_image_model(model):
        debug.log("model.reject_image_model", session_id=session_id, model=model)
        await codex_gpt.finish(
            _reply(event) + "图片模型请通过 #image 使用；#gpt 会话模型只用于对话。"
        )
        return

    await store.set_model(session_id, model)
    debug.log("model.changed", session_id=session_id, model=model)
    await codex_gpt.finish(_reply(event) + f"当前会话模型已切换为：{model}")


async def _handle_models_command(event: MessageEvent) -> None:
    try:
        models = await _get_models(refresh=True)
    except (CodexGPTError, httpx.HTTPError) as exc:
        debug.log("models.command_error", error=_safe_error(exc))
        await codex_gpt.finish(_reply(event) + f"获取模型列表失败：{_safe_error(exc)}")
        return

    if not models:
        debug.log("models.command_empty")
        await codex_gpt.finish(_reply(event) + "模型列表为空。")
        return

    visible = models[:30]
    debug.log("models.command_success", total=len(models), visible=len(visible))
    suffix = f"\n... 还有 {len(models) - len(visible)} 个" if len(models) > len(visible) else ""
    await codex_gpt.finish(_reply(event) + "可用模型：\n" + "\n".join(visible) + suffix)


async def _send_model_list(event: MessageEvent, keyword: str = "") -> None:
    try:
        all_models = await _get_models(refresh=True)
    except (CodexGPTError, httpx.HTTPError) as exc:
        debug.log("model.list_error", keyword=keyword, error=_safe_error(exc))
        await codex_gpt.finish(_reply(event) + f"获取模型列表失败：{_safe_error(exc)}")
        return

    indexed_models = list(enumerate(all_models, start=1))
    if keyword:
        key = keyword.lower()
        indexed_models = [(index, model) for index, model in indexed_models if key in model.lower()]

    if not indexed_models:
        debug.log("model.list_empty", keyword=keyword, total=len(all_models))
        await codex_gpt.finish(_reply(event) + "没有匹配的模型。")
        return

    visible = indexed_models[:60]
    debug.log(
        "model.list_success",
        keyword=keyword,
        total=len(all_models),
        matched=len(indexed_models),
        visible=len(visible),
    )
    lines = [f"{index}. {model}" for index, model in visible]
    suffix = (
        f"\n... 还有 {len(indexed_models) - len(visible)} 个，使用关键词继续过滤"
        if len(indexed_models) > len(visible)
        else ""
    )
    await codex_gpt.finish(
        _reply(event)
        + "可用模型：\n"
        + "\n".join(lines)
        + suffix
        + "\n\nsuperuser 可用：#gpt model use <编号|模型名>"
    )


async def _get_models(refresh: bool = False) -> list[str]:
    global _model_cache, _model_cache_at
    now = time.time()
    if not refresh and _model_cache and now - _model_cache_at < _MODEL_CACHE_TTL:
        debug.log("models.cache_hit", count=len(_model_cache))
        return _model_cache

    debug.log("models.cache_refresh", refresh=refresh, cached_count=len(_model_cache))
    models = await client.list_models()
    _model_cache = models
    _model_cache_at = now
    debug.log("models.cache_updated", count=len(models))
    return models


async def _resolve_model_arg(value: str) -> str:
    if not value:
        raise CodexGPTError("请指定模型编号或模型名。")

    models = await _get_models(refresh=False)
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(models):
            debug.log("model.resolve_index", value=value, model=models[index - 1])
            return models[index - 1]
        raise CodexGPTError(f"模型编号超出范围：{value}")

    if value in models:
        debug.log("model.resolve_exact", value=value)
        return value

    lowered = value.lower()
    matches = [model for model in models if lowered in model.lower()]
    if len(matches) == 1:
        debug.log("model.resolve_fuzzy", value=value, model=matches[0])
        return matches[0]
    if len(matches) > 1:
        preview = "\n".join(f"- {model}" for model in matches[:10])
        raise CodexGPTError(f"匹配到多个模型，请使用编号或完整模型名：\n{preview}")
    raise CodexGPTError(f"模型不在 /v1/models 列表中：{value}")


async def _handle_system_command(session_id: str, rest: str, event: MessageEvent) -> None:
    if not rest:
        session = await store.get(session_id)
        prompt = session.system_prompt or config.default_system_prompt
        source = "自定义" if session.system_prompt else "默认"
        await codex_gpt.finish(
            _reply(event) + f"当前 system prompt（{source}）：\n{_shorten(prompt, 1200)}"
        )
        return

    if rest.lower() in {"default", "reset", "默认", "重置"}:
        await store.set_system_prompt(session_id, None)
        await codex_gpt.finish(_reply(event) + "已恢复默认 system prompt。")
        return

    await store.set_system_prompt(session_id, rest.strip())
    await codex_gpt.finish(_reply(event) + "已更新当前会话的 system prompt。")


async def _status_text(session_id: str) -> str:
    session = await store.get(session_id)
    model = session.model or config.default_model
    system_source = "自定义" if session.system_prompt else "默认"
    rounds = len(session.messages) // 2
    updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session.updated_at))
    return (
        "Codex GPT 状态\n"
        f"会话：{session_id}\n"
        f"模型：{model}\n"
        f"system prompt：{system_source}\n"
        f"上下文轮数：{rounds}\n"
        f"上下文上限：{config.max_history_messages} 条 / {config.max_history_chars} 字符\n"
        f"最后更新：{updated}"
    )


def _extract_payload(event: MessageEvent) -> str:
    match = CMD_PATTERN.match(event.message.extract_plain_text().strip())
    if not match:
        return ""
    return match.group(3).strip()


def _extract_image_payload(event: MessageEvent) -> str:
    match = IMAGE_CMD_PATTERN.match(event.message.extract_plain_text().strip())
    if not match:
        return ""
    return match.group(3).strip()


def _is_codex_command(text: str) -> bool:
    normalized = text.strip()
    return bool(CMD_PATTERN.match(normalized) or IMAGE_CMD_PATTERN.match(normalized))


def _active_message_text(event: GroupMessageEvent, at_bot: bool = False) -> str:
    text = event.message.extract_plain_text().strip()
    image_count = sum(1 for segment in event.message if _segment_type(segment) == "image")
    if image_count:
        marker = " ".join("[图片]" for _ in range(image_count))
        text = f"{text} {marker}".strip()
    if at_bot and not text:
        text = "[有人@了我]"
    return text


def _is_at_bot(event: GroupMessageEvent, bot: Bot) -> bool:
    if _event_to_me(event):
        return True

    self_id = str(bot.self_id)
    for message in _event_messages(event):
        try:
            segments = tuple(message)
        except TypeError:
            segments = ()

        for segment in segments:
            if _segment_type(segment) != "at":
                continue
            qq = _segment_data(segment).get("qq")
            if qq is not None and str(qq) == self_id:
                return True

        if re.search(rf"\[CQ:at,qq={re.escape(self_id)}(?:,|\])", str(message)):
            return True
    return False


def _event_to_me(event: GroupMessageEvent) -> bool:
    to_me = getattr(event, "to_me", None)
    if isinstance(to_me, bool):
        return to_me

    is_tome = getattr(event, "is_tome", None)
    if callable(is_tome):
        try:
            return bool(is_tome())
        except Exception:
            return False

    return False


def _event_messages(event: GroupMessageEvent) -> tuple[object, ...]:
    messages: list[object] = [event.message]
    original_message = getattr(event, "original_message", None)
    if original_message is not None and original_message is not event.message:
        messages.append(original_message)
    return tuple(messages)


def _segment_type(segment: object) -> str | None:
    segment_type = getattr(segment, "type", None)
    if isinstance(segment_type, str):
        return segment_type
    if isinstance(segment, dict):
        value = segment.get("type")
        if isinstance(value, str):
            return value
    return None


def _segment_data(segment: object) -> dict[str, object]:
    data = getattr(segment, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(segment, dict):
        value = segment.get("data")
        if isinstance(value, dict):
            return value
    return {}


def _active_module_allowed(event: GroupMessageEvent) -> tuple[bool, str]:
    user_id = int(event.user_id)
    if str(user_id) in get_driver().config.superusers:
        return True, "superuser"

    group_id = str(event.group_id)
    if group_config.is_user_banned(group_id, user_id):
        return False, "user_banned"
    if not group_config.is_module_enabled(group_id, "codex_gpt"):
        return False, "module_disabled"
    return True, "module_enabled"


def _event_nickname(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    for attr in ("card", "nickname"):
        value = getattr(sender, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(event.user_id)


def _remember_active_message(event: GroupMessageEvent, text: str, nickname: str) -> None:
    group_id = int(event.group_id)
    memory = _active_memories.setdefault(
        group_id, deque(maxlen=config.active_reply_memory_messages)
    )
    memory.append((time.time(), int(event.user_id), nickname, _shorten(text, 700)))


def _active_rate_allowed(group_id: int) -> tuple[bool, int]:
    reply_times = _pruned_active_reply_times(group_id)
    return len(reply_times) < config.active_reply_rate_limit, len(reply_times)


def _record_active_reply(group_id: int) -> int:
    reply_times = _pruned_active_reply_times(group_id)
    reply_times.append(time.time())
    return len(reply_times)


def _pruned_active_reply_times(group_id: int) -> deque[float]:
    window_seconds = config.active_reply_rate_window_minutes * 60.0
    cutoff = time.time() - window_seconds
    reply_times = _active_reply_times.setdefault(group_id, deque())
    while reply_times and reply_times[0] < cutoff:
        reply_times.popleft()
    return reply_times


def _load_group_history(group_id: int, limit: int) -> list[tuple[str, int, str]]:
    if limit <= 0:
        return []

    try:
        rows = load_recent_group_history(group_id, limit)
    except Exception as exc:
        debug.log("active.history_load_error", group_id=group_id, error=_safe_error(exc))
        return []

    history: list[tuple[str, int, str]] = []
    for timestamp_text, user_id, content in rows:
        text = _shorten(str(content or "").strip(), 700)
        if not text:
            continue
        history.append((str(timestamp_text), int(user_id), text))
    return history


async def _build_active_prompt(group_id: int, current_text: str, nickname: str) -> str:
    history = await asyncio.to_thread(
        _load_group_history, group_id, config.active_reply_history_messages
    )
    memory = list(_active_memories.get(group_id, ()))
    lines: list[str] = []
    seen: set[tuple[str, int, str]] = set()

    for timestamp_text, user_id, text in history:
        ts = timestamp_text[-5:]
        key = (ts, user_id, text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"[{ts} {user_id}]: {text}")

    for timestamp, user_id, name, text in memory:
        ts = time.strftime("%H:%M", time.localtime(timestamp))
        key = (ts, user_id, text)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"[{ts} {name}({user_id})]: {text}")

    transcript = "\n".join(lines)
    if len(transcript) > config.active_reply_max_prompt_chars:
        transcript = transcript[-config.active_reply_max_prompt_chars :]

    debug.log(
        "active.history_ready",
        group_id=group_id,
        persistent_count=len(history),
        memory_count=len(memory),
        line_count=len(lines),
        transcript_chars=len(transcript),
    )

    return (
        "你正在一个群聊里被概率触发进行自然回复。请根据最近聊天内容判断怎样接话，"
        "直接输出要发送到群里的内容。\n"
        "要求：简短、自然、不要解释触发规则、不要复述完整上下文；如果上下文里有明确问题就回答，"
        "否则像普通群友一样轻量接一句。\n\n"
        f"最近群聊记录：\n{transcript}\n\n"
        f"当前触发消息来自 {nickname}：{current_text}"
    )


def _session_id(event: MessageEvent) -> str:
    scope = config.session_scope
    if isinstance(event, GroupMessageEvent):
        if scope == "group":
            return f"group:{event.group_id}"
        return f"group:{event.group_id}:user:{event.user_id}"
    return f"private:{event.user_id}"


def _split_first(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _reply(event: MessageEvent) -> MessageSegment:
    return MessageSegment.reply(event.message_id)


async def _delete_message(bot: Bot, message_id: int) -> None:
    try:
        await bot.delete_msg(message_id=message_id)
    except Exception:
        pass


async def _send_long_reply(event: MessageEvent, text: str, bot: Bot | None = None) -> None:
    chunks = _split_chunks(text, 3200)
    if not chunks:
        chunks = ["（空回复）"]

    if bot is None:
        await codex_gpt.send(_reply(event) + chunks[0])
        for chunk in chunks[1:]:
            await codex_gpt.send(chunk)
        return

    await bot.send(event, _reply(event) + chunks[0])
    for chunk in chunks[1:]:
        await bot.send(event, chunk)


async def _download_event_images(
    event: MessageEvent,
    session_id: str,
    bot: Bot | None = None,
) -> list[bytes]:
    try:
        images = await download_all_images_from_event(event, bot=bot)
    except Exception as exc:
        debug.log("images.download_error", session_id=session_id, error=_safe_error(exc))
        return []
    if images:
        debug.log(
            "images.downloaded",
            session_id=session_id,
            count=len(images),
            bytes=sum(len(image) for image in images),
        )
    return images


def _store_codex_image_assets(
    *,
    source: str,
    images: list[bytes],
    session_id: str,
    prompt: str,
    event: MessageEvent,
    verify_hash_collision: bool = False,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, image_data in enumerate(images):
        record = _store_codex_image_asset(
            source=source,
            image_data=image_data,
            session_id=session_id,
            prompt=prompt,
            event=event,
            input_index=index,
            verify_hash_collision=verify_hash_collision,
        )
        if record:
            records.append(record)
    return records


def _store_codex_image_asset(
    *,
    source: str,
    image_data: bytes,
    session_id: str,
    prompt: str,
    event: MessageEvent,
    input_index: int | None = None,
    model: str | None = None,
    mime_type: str | None = None,
    revised_prompt: str | None = None,
    text: str | None = None,
    verify_hash_collision: bool = False,
) -> dict[str, object] | None:
    try:
        detected_mime = mime_type or _guess_image_mime(image_data)
        ext = _extension_from_mime(detected_mime)
        original_name = _codex_image_original_name(source, input_index, ext)
        metadata: dict[str, object] = {
            "media_source": source,
            "session_id": session_id,
            "prompt": prompt,
            "model": model or config.image_model,
            "visibility": False,
            "group": getattr(event, "group_id", 0) or 0,
            "qq": getattr(event, "user_id", 0) or 0,
            "tags": ["codex_gpt", "gptimage2", source],
        }
        if input_index is not None:
            metadata["input_index"] = input_index
        if revised_prompt:
            metadata["revised_prompt"] = revised_prompt
        if text:
            metadata["text"] = text

        record = image_storage.upload(
            image_data,
            ext=ext,
            original_name=original_name,
            verify_hash_collision=verify_hash_collision,
            **metadata,
        )
        debug.log(
            "image.asset_stored",
            session_id=session_id,
            source=source,
            file_id=record.get("file_id"),
            status=record.get("status"),
        )
        return record
    except Exception as exc:
        logger.warning("[codex_gpt] failed to store %s image: %s", source, exc)
        debug.log(
            "image.asset_store_failed",
            session_id=session_id,
            source=source,
            error=_safe_error(exc),
        )
        return None


def _extension_from_mime(mime_type: str) -> str:
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if normalized == "image/jpeg":
        return ".jpg"
    if normalized.startswith("image/"):
        return "." + normalized.removeprefix("image/")
    return ".png"


def _codex_image_original_name(source: str, input_index: int | None, ext: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if input_index is None:
        return f"{source}_{timestamp}{ext}"
    return f"{source}_{timestamp}_{input_index + 1:02d}{ext}"


def _is_image_model(model: str) -> bool:
    normalized = model.lower().removeprefix("openai/")
    return normalized.startswith("gpt-image") or normalized.startswith("dall-e")


def _attach_images_to_latest_user_message(
    messages: list[dict[str, object]], images: list[bytes]
) -> list[dict[str, object]]:
    if not images:
        return messages

    converted = [dict(message) for message in messages]
    for message in reversed(converted):
        if message.get("role") != "user":
            continue
        text = str(message.get("content") or "")
        content: list[dict[str, object]] = [{"type": "text", "text": text}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            mime_type = _guess_image_mime(image)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        message["content"] = content
        return converted
    return converted


def _guess_image_mime(data: bytes) -> str:
    kind = imghdr.what(None, data)
    if kind == "jpg":
        kind = "jpeg"
    if kind in {"png", "jpeg", "gif", "webp", "bmp"}:
        return f"image/{kind}"
    return "image/png"


def _split_chunks(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.splitlines(keepends=True):
        if len(paragraph) > limit:
            if current:
                chunks.append("".join(current).strip())
                current = []
                current_len = 0
            chunks.extend(paragraph[i : i + limit] for i in range(0, len(paragraph), limit))
            continue
        if current_len + len(paragraph) > limit:
            chunks.append("".join(current).strip())
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += len(paragraph)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _help_text() -> str:
    return (
        "Codex GPT 用法\n"
        "#gpt <问题>：带上下文对话\n"
        "#gpt one <问题>：单次提问，不写入上下文\n"
        "#gpt <问题> + 图片：按视觉输入对话\n"
        "#gpt + 图片：默认描述图片\n"
        "#image <描述>：生成图片\n"
        "#image <描述> + 图片：基于图片编辑/创作\n"
        "#gpt clear：清空当前会话\n"
        "#gpt forget：忘记上一轮对话\n"
        "#gpt status：查看当前会话状态\n"
        "#gpt model：查看当前模型和模型命令\n"
        "#gpt model list [关键词]：从 /v1/models 拉取可用模型\n"
        "#gpt model use <编号|模型名>：superuser 切换当前会话模型\n"
        "#gpt model reset：superuser 恢复默认模型\n"
        "#gpt models：拉取可用模型列表\n"
        "#gpt system [设定]：查看或设置当前会话 system prompt\n"
        "别名：#codex / #chat"
    )


def _image_help_text() -> str:
    return (
        "图片生成用法\n"
        "#image <描述>：生成图片\n"
        "#image <描述> + 图片：基于图片编辑/创作\n"
        "#image + 图片：默认基于图片内容创作\n"
        "别名：#img\n"
        f"当前图片模型：{config.image_model}"
    )


def _model_help_text(model: str) -> str:
    return (
        f"当前模型：{model}\n"
        "模型命令：\n"
        "#gpt model list [关键词]\n"
        "#gpt model use <编号|模型名>\n"
        "#gpt model reset\n"
        "切换模型仅限 superuser。"
    )


def _not_configured_text() -> str:
    return (
        "Codex GPT 尚未配置 API Key。\n"
        "请在项目 .env 或插件 .env 中设置 CODEX_GPT_API_KEY 后再使用。"
    )


def _safe_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if config.api_key:
        text = text.replace(config.api_key, "***")
    return _shorten(text, 700)


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
