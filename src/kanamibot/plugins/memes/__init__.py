from __future__ import annotations

import asyncio
import importlib
import json
import os
from argparse import Namespace
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any

from nonebot import on_shell_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import ShellCommandArgs
from nonebot.plugin import PluginMetadata
from nonebot.rule import ArgumentParser

from kanamibot.core import ModuleRule
from kanamibot.core.paths import DATA_DIR
from kanamibot.core.utils.image import download_all_images_from_event

MODULE_NAME = "memes"
GROUP_RULE = ModuleRule(MODULE_NAME)
MEME_HOME_ENV = "MEME_HOME"
DEFAULT_MEME_HOME = DATA_DIR / "memes"
os.environ.setdefault(MEME_HOME_ENV, str(DEFAULT_MEME_HOME))
MEME_ENGINE = importlib.import_module("meme_generator")
MEME_RESOURCES = importlib.import_module("meme_generator.resources")
MEME_HOME = Path(os.environ[MEME_HOME_ENV])
RESOURCE_IMAGES_DIR = MEME_HOME / "resources" / "images"
RESOURCE_UPDATE_LOCK = asyncio.Lock()
LIST_LIMIT = 20
ShellArgs = Annotated[Namespace, ShellCommandArgs()]

__plugin_meta__ = PluginMetadata(
    name="Memes",
    description="通过 MemeCrafters/meme-generator 生成表情包。",
    usage=(
        "meme <模板key> [文本...] [-t 文本]... [-a key=value] [--args-json JSON]\n"
        "meme_info <模板key>\n"
        "meme_list [关键词]\n"
        "meme_update\n"
        "图片输入支持当前消息、回复消息和合并转发中的图片。"
    ),
)


class MemeError(RuntimeError):
    pass


def _normalise_meme_info(info: Any) -> dict[str, Any]:
    params = _field(info, "params", {})
    shortcuts = []
    for shortcut in _field(info, "shortcuts", []) or []:
        display = _field(shortcut, "humanized") or _field(shortcut, "pattern", "")
        if display:
            shortcuts.append({"key": str(display)})
    return {
        "key": str(_field(info, "key", "")),
        "params_type": {
            "min_images": _get_count(params, "min_images"),
            "max_images": _get_count(params, "max_images"),
            "min_texts": _get_count(params, "min_texts"),
            "max_texts": _get_count(params, "max_texts"),
            "default_texts": _as_text_list(_field(params, "default_texts", [])),
        },
        "keywords": _as_text_list(_field(info, "keywords", [])),
        "shortcuts": shortcuts,
        "tags": set(_field(info, "tags", set()) or set()),
    }


def _get_meme(key: str) -> Any:
    meme = MEME_ENGINE.get_meme(key)
    if meme is None:
        raise MemeError(f"表情模板 {key!r} 不存在，请使用 meme_list 搜索。")
    return meme


def _engine_error_message(result: Any) -> str:
    error_type = type(result).__name__
    if error_type == "ImageDecodeError":
        return f"图片解码失败：{_field(result, 'error', '')}"
    if error_type == "ImageEncodeError":
        return f"图片编码失败：{_field(result, 'error', '')}"
    if error_type == "ImageAssetMissing":
        path = _field(result, "path", "未知素材")
        return f"表情素材缺失：{path}。请稍后重试，或发送 meme_update 重新同步素材。"
    if error_type == "DeserializeError":
        return f"模板参数解析失败：{_field(result, 'error', '')}"
    if error_type == "ImageNumberMismatch":
        return (
            "图片数量不符："
            f"需要 {_count_range_text(_field(result, 'min', 0), _field(result, 'max', 0))} 张，"
            f"实际 {_field(result, 'actual', 0)} 张。"
        )
    if error_type == "TextNumberMismatch":
        return (
            "文本数量不符："
            f"需要 {_count_range_text(_field(result, 'min', 0), _field(result, 'max', 0))} 段，"
            f"实际 {_field(result, 'actual', 0)} 段。"
        )
    if error_type == "TextOverLength":
        return f"文本过长：{_field(result, 'text', '')}"
    if error_type == "MemeFeedback":
        return str(_field(result, "feedback", "表情生成失败。"))
    return f"表情生成失败：{result!r}"


async def _update_resources() -> None:
    async with RESOURCE_UPDATE_LOCK:
        try:
            await asyncio.to_thread(MEME_RESOURCES.check_resources)
        except Exception as exc:
            raise MemeError(f"表情素材同步失败：{exc}") from exc


def _resource_count() -> int:
    if not RESOURCE_IMAGES_DIR.is_dir():
        return 0
    return sum(1 for path in RESOURCE_IMAGES_DIR.rglob("*") if path.is_file())


async def _get_meme_info(key: str) -> dict[str, Any]:
    return _normalise_meme_info(_get_meme(key).info)


async def _get_meme_keys() -> list[str]:
    return [str(key) for key in MEME_ENGINE.get_meme_keys()]


async def _get_infos(keys: Sequence[str]) -> list[dict[str, Any]]:
    infos = []
    for key in keys:
        meme = MEME_ENGINE.get_meme(key)
        if meme is not None:
            infos.append(_normalise_meme_info(meme.info))
    return infos


def _generate_once(
    key: str,
    images: Sequence[bytes],
    texts: Sequence[str],
    args: dict[str, Any],
    image_name: str,
) -> Any:
    meme = _get_meme(key)
    meme_images = [MEME_ENGINE.Image(image_name, image) for image in images]
    return meme.generate(meme_images, list(texts), args)


async def _generate_meme(
    key: str,
    images: Sequence[bytes],
    texts: Sequence[str],
    args: dict[str, Any],
    image_name: str,
) -> bytes:
    try:
        result = await asyncio.to_thread(_generate_once, key, images, texts, args, image_name)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise MemeError(f"表情生成失败：{exc}") from exc
    if isinstance(result, bytes):
        return result

    if type(result).__name__ == "ImageAssetMissing":
        await _update_resources()
        try:
            result = await asyncio.to_thread(_generate_once, key, images, texts, args, image_name)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise MemeError(f"表情生成失败：{exc}") from exc
        if isinstance(result, bytes):
            return result

    raise MemeError(_engine_error_message(result))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _params(meme_info: Any) -> Any:
    return _field(meme_info, "params_type", {})


def _count_range_text(min_value: int, max_value: int) -> str:
    if min_value == max_value:
        return str(min_value)
    return f"{min_value}-{max_value}"


def _get_count(params: Any, name: str, default: int = 0) -> int:
    try:
        return int(_field(params, name, default) or default)
    except (TypeError, ValueError):
        return default


def _as_text_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, Iterable):
        return [str(value) for value in values if str(value)]
    return [str(values)]


def _format_keywords(values: Any, limit: int = 8) -> str:
    items = _as_text_list(values)
    if not items:
        return "无"
    shown = items[:limit]
    suffix = f" 等 {len(items)} 个" if len(items) > limit else ""
    return "、".join(shown) + suffix


def _meme_search_text(meme_info: dict[str, Any]) -> str:
    fields = [
        _field(meme_info, "key", ""),
        *_as_text_list(_field(meme_info, "keywords", [])),
        *_as_text_list(_field(meme_info, "tags", [])),
    ]
    return " ".join(fields).casefold()


def _format_meme_line(meme_info: dict[str, Any]) -> str:
    params = _params(meme_info)
    key = str(_field(meme_info, "key", ""))
    keywords = _format_keywords(_field(meme_info, "keywords", []), limit=3)
    images = _count_range_text(_get_count(params, "min_images"), _get_count(params, "max_images"))
    texts = _count_range_text(_get_count(params, "min_texts"), _get_count(params, "max_texts"))
    return f"{key} | 图 {images} | 文 {texts} | {keywords}"


def _format_key_line(key: str) -> str:
    return f"{key} | meme_info {key} 查看详情"


def _format_meme_info(meme_info: dict[str, Any]) -> str:
    params = _params(meme_info)
    key = str(_field(meme_info, "key", ""))
    images = _count_range_text(_get_count(params, "min_images"), _get_count(params, "max_images"))
    texts = _count_range_text(_get_count(params, "min_texts"), _get_count(params, "max_texts"))
    default_texts = _format_keywords(_field(params, "default_texts", []), limit=6)
    keywords = _format_keywords(_field(meme_info, "keywords", []), limit=10)
    tags = _format_keywords(sorted(_field(meme_info, "tags", []) or []), limit=10)

    shortcuts = []
    for shortcut in _field(meme_info, "shortcuts", []) or []:
        key_text = _field(shortcut, "key", "")
        if key_text:
            shortcuts.append(str(key_text))

    lines = [
        f"模板：{key}",
        f"关键词：{keywords}",
        f"标签：{tags}",
        f"需要图片：{images}",
        f"需要文本：{texts}",
    ]
    if default_texts != "无":
        lines.append(f"默认文本：{default_texts}")
    if shortcuts:
        lines.append(f"快捷词：{_format_keywords(shortcuts, limit=10)}")
    lines.append(f"示例：meme {key} 文本")
    return "\n".join(lines)


def _parse_arg_value(raw_value: str) -> Any:
    value = raw_value.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _parse_extra_args(raw_items: Sequence[str], raw_json: str | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if raw_json:
        try:
            loaded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--args-json 不是合法 JSON：{exc.msg}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("--args-json 必须是 JSON 对象")
        parsed.update(loaded)

    for raw_item in raw_items:
        if "=" not in raw_item:
            raise ValueError(f"参数 {raw_item!r} 缺少 '='，请使用 -a key=value")
        key, value = raw_item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数 {raw_item!r} 的 key 不能为空")
        parsed[key] = _parse_arg_value(value)

    invalid_keys = [
        key
        for key, value in parsed.items()
        if not isinstance(value, (bool, str, int, float))
    ]
    if invalid_keys:
        raise ValueError(f"模板参数只支持布尔、字符串或数字：{', '.join(invalid_keys)}")
    return parsed


def _sender_name(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    for attr in ("card", "nickname"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return str(event.user_id)


def _texts_from_args(
    meme_info: dict[str, Any],
    plain_texts: Sequence[str],
    explicit_texts: Sequence[str],
) -> list[str]:
    params = _params(meme_info)
    min_texts = _get_count(params, "min_texts")
    max_texts = _get_count(params, "max_texts")

    explicit = [text.strip() for text in explicit_texts if text and text.strip()]
    plain = [text.strip() for text in plain_texts if text and text.strip()]
    if explicit:
        texts = [*explicit, *plain]
    elif max_texts <= 1:
        joined = " ".join(plain).strip()
        texts = [joined] if joined else []
    else:
        texts = plain

    if not texts:
        default_texts = _as_text_list(_field(params, "default_texts", []))
        if min_texts <= len(default_texts) <= max_texts:
            return default_texts
    return texts


def _select_images(meme_info: dict[str, Any], images: list[bytes]) -> list[bytes]:
    params = _params(meme_info)
    min_images = _get_count(params, "min_images")
    max_images = _get_count(params, "max_images")
    if max_images == 0:
        return []
    if len(images) > max_images:
        return images[:max_images]
    if len(images) < min_images:
        return images
    return images


def _validate_counts(
    meme_info: dict[str, Any],
    images: list[bytes],
    texts: list[str],
) -> str | None:
    params = _params(meme_info)
    key = _field(meme_info, "key", "")
    min_images = _get_count(params, "min_images")
    max_images = _get_count(params, "max_images")
    min_texts = _get_count(params, "min_texts")
    max_texts = _get_count(params, "max_texts")

    if len(images) < min_images:
        image_range = _count_range_text(min_images, max_images)
        return f"模板 {key} 需要 {image_range} 张图片，请发送或回复图片。"
    if len(images) > max_images:
        return f"模板 {key} 最多使用 {max_images} 张图片。"
    if len(texts) < min_texts:
        return f"模板 {key} 需要 {_count_range_text(min_texts, max_texts)} 段文本。"
    if len(texts) > max_texts:
        return f"模板 {key} 最多使用 {max_texts} 段文本；多词文本请加引号或使用 -t。"
    return None


meme_parser = ArgumentParser(description="使用 meme-generator 制作表情包")
meme_parser.add_argument("key", help="模板 key，可用 meme_list 搜索")
meme_parser.add_argument("texts", nargs="*", help="模板文本。多段文本建议使用 -t")
meme_parser.add_argument(
    "-t",
    "--text",
    dest="explicit_texts",
    action="append",
    default=[],
    help="追加一段文本",
)
meme_parser.add_argument(
    "-a",
    "--arg",
    dest="arg_items",
    action="append",
    default=[],
    help="模板参数，格式 key=value",
)
meme_parser.add_argument("--args-json", dest="args_json", default=None, help="模板参数 JSON 对象")

meme_matcher = on_shell_command(
    "meme",
    aliases={"表情包", "做表情", "生成表情"},
    parser=meme_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@meme_matcher.handle()
async def handle_meme(
    bot: Bot,
    event: MessageEvent,
    matcher: Matcher,
    args: ShellArgs,
) -> None:
    try:
        meme_info = await _get_meme_info(args.key)
    except MemeError as exc:
        await matcher.finish(str(exc))

    try:
        extra_args = _parse_extra_args(args.arg_items, args.args_json)
    except ValueError as exc:
        await matcher.finish(str(exc))

    images = _select_images(meme_info, await download_all_images_from_event(event, bot=bot))
    texts = _texts_from_args(meme_info, args.texts, args.explicit_texts)
    if message := _validate_counts(meme_info, images, texts):
        await matcher.finish(message)

    if not RESOURCE_IMAGES_DIR.is_dir():
        await matcher.send("首次使用需要同步约 400 MB 表情素材，正在下载，请稍候。")

    try:
        result = await _generate_meme(
            args.key,
            images=images,
            texts=texts,
            args=extra_args,
            image_name=_sender_name(event),
        )
    except MemeError as exc:
        await matcher.finish(str(exc))

    await matcher.finish(MessageSegment.image(result))


info_parser = ArgumentParser(description="查看表情模板信息")
info_parser.add_argument("key", help="模板 key")

meme_info_matcher = on_shell_command(
    "meme_info",
    aliases={"表情详情", "表情信息"},
    parser=info_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@meme_info_matcher.handle()
async def handle_meme_info(matcher: Matcher, args: ShellArgs) -> None:
    try:
        meme_info = await _get_meme_info(args.key)
    except MemeError as exc:
        await matcher.finish(str(exc))
    await matcher.finish(_format_meme_info(meme_info))


list_parser = ArgumentParser(description="搜索表情模板")
list_parser.add_argument("query", nargs="*", help="模板 key、关键词或标签")

meme_list_matcher = on_shell_command(
    "meme_list",
    aliases={"表情列表", "表情搜索"},
    parser=list_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@meme_list_matcher.handle()
async def handle_meme_list(matcher: Matcher, args: ShellArgs) -> None:
    try:
        keys = await _get_meme_keys()
    except MemeError as exc:
        await matcher.finish(str(exc))

    query = " ".join(args.query).strip().casefold()
    if not query:
        lines = [_format_key_line(key) for key in sorted(keys)[:LIST_LIMIT]]
        if len(keys) > LIST_LIMIT:
            lines.append(f"... 还有 {len(keys) - LIST_LIMIT} 个模板，请加关键词搜索。")
        await matcher.finish("可用表情模板：\n" + "\n".join(lines))

    key_matches = [key for key in keys if query in key.casefold()]
    infos = await _get_infos(keys)
    matched_infos = [info for info in infos if query in _meme_search_text(info)]
    known = {str(_field(info, "key", "")) for info in matched_infos}
    matched_infos.extend(
        {"key": key, "keywords": [], "tags": [], "params_type": {}}
        for key in key_matches
        if key not in known
    )

    if not matched_infos:
        await matcher.finish("没有找到匹配的表情模板。")

    matched_infos = sorted(matched_infos, key=lambda item: str(_field(item, "key", "")))
    lines = [_format_meme_line(meme_info) for meme_info in matched_infos[:LIST_LIMIT]]
    if len(matched_infos) > LIST_LIMIT:
        lines.append(f"... 还有 {len(matched_infos) - LIST_LIMIT} 个结果，请加关键词缩小范围。")
    await matcher.finish("可用表情模板：\n" + "\n".join(lines))


meme_update_matcher = on_shell_command(
    "meme_update",
    aliases={"更新表情素材", "同步表情素材"},
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@meme_update_matcher.handle()
async def handle_meme_update(matcher: Matcher) -> None:
    before = await asyncio.to_thread(_resource_count)
    await matcher.send("正在检查并同步表情素材，首次下载约 400 MB，请稍候。")
    try:
        await _update_resources()
    except MemeError as exc:
        await matcher.finish(str(exc))
    after = await asyncio.to_thread(_resource_count)
    if after == 0:
        await matcher.finish("表情素材同步失败，请检查网络后重试。")
    added = max(after - before, 0)
    await matcher.finish(f"表情素材已就绪：{after} 个文件（本次新增 {added} 个）。")
