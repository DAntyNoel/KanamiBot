from __future__ import annotations

import asyncio
import json
import os
from argparse import Namespace
from collections.abc import Iterable, Sequence
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from nonebot import on_shell_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import ShellCommandArgs
from nonebot.plugin import PluginMetadata
from nonebot.rule import ArgumentParser

from kanamibot.core import ModuleRule
from kanamibot.core.utils.image import download_all_images_from_event

MODULE_NAME = "memes"
GROUP_RULE = ModuleRule(MODULE_NAME)
BASE_URL_ENV = "MEME_GENERATOR_BASE_URL"
DEFAULT_BASE_URL = "http://127.0.0.1:2233"
REQUEST_TIMEOUT = 60
LIST_LIMIT = 20
INFO_CONCURRENCY = 8
ShellArgs = Annotated[Namespace, ShellCommandArgs()]

__plugin_meta__ = PluginMetadata(
    name="Memes",
    description="通过 MemeCrafters/meme-generator API 生成表情包。",
    usage=(
        "meme <模板key> [文本...] [-t 文本]... [-a key=value] [--args-json JSON]\n"
        "meme_info <模板key>\n"
        "meme_list [关键词]\n"
        "图片输入支持当前消息、回复消息和合并转发中的图片。\n"
        f"API 地址由 {BASE_URL_ENV} 配置，默认 {DEFAULT_BASE_URL}。"
    ),
)


class MemeApiError(RuntimeError):
    pass


def _base_url() -> str:
    return (os.getenv(BASE_URL_ENV) or DEFAULT_BASE_URL).strip().rstrip("/")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base_url(), timeout=REQUEST_TIMEOUT)


def _api_error_message(resp: httpx.Response) -> str:
    detail = ""
    try:
        payload = resp.json()
    except ValueError:
        detail = resp.text.strip()
    else:
        raw_detail = payload.get("detail") if isinstance(payload, dict) else payload
        if isinstance(raw_detail, str):
            detail = raw_detail
        elif raw_detail is not None:
            detail = json.dumps(raw_detail, ensure_ascii=False)
    detail = detail[:300] if detail else resp.reason_phrase
    return f"meme-generator API 返回 {resp.status_code}：{detail}"


def _network_error_message(exc: httpx.HTTPError) -> str:
    return f"无法连接 meme-generator API（{BASE_URL_ENV}={_base_url()}）：{exc}"


async def _request_json(client: httpx.AsyncClient, path: str) -> Any:
    try:
        resp = await client.get(path)
    except httpx.HTTPError as exc:
        raise MemeApiError(_network_error_message(exc)) from exc
    if resp.status_code >= 400:
        raise MemeApiError(_api_error_message(resp))
    return resp.json()


async def _get_meme_info(key: str) -> dict[str, Any]:
    async with _client() as client:
        payload = await _request_json(client, f"/memes/{quote(key, safe='')}/info")
    if not isinstance(payload, dict):
        raise MemeApiError("meme-generator API 返回了无法识别的模板信息。")
    return payload


async def _get_meme_keys() -> list[str]:
    async with _client() as client:
        payload = await _request_json(client, "/memes/keys")
    if not isinstance(payload, list):
        raise MemeApiError("meme-generator API 返回了无法识别的模板列表。")
    return [str(key) for key in payload]


async def _get_infos(keys: Sequence[str]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(INFO_CONCURRENCY)

    async with _client() as client:
        async def fetch(key: str) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    payload = await _request_json(client, f"/memes/{quote(key, safe='')}/info")
                except MemeApiError:
                    return None
                return payload if isinstance(payload, dict) else None

        results = await asyncio.gather(*(fetch(key) for key in keys))
    return [info for info in results if info]


async def _generate_meme(
    key: str,
    images: Sequence[bytes],
    texts: Sequence[str],
    args: dict[str, Any],
) -> bytes:
    form_data: dict[str, Any] = {"args": json.dumps(args, ensure_ascii=False)}
    if texts:
        form_data["texts"] = list(texts)
    files = [
        ("images", (f"image{index}.png", image, "application/octet-stream"))
        for index, image in enumerate(images)
    ]

    async with _client() as client:
        try:
            resp = await client.post(
                f"/memes/{quote(key, safe='')}/",
                data=form_data,
                files=files or None,
            )
        except httpx.HTTPError as exc:
            raise MemeApiError(_network_error_message(exc)) from exc

    if resp.status_code >= 400:
        raise MemeApiError(_api_error_message(resp))
    return resp.content


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
    return parsed


def _sender_name(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    for attr in ("card", "nickname"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return str(event.user_id)


def _build_meme_args(event: MessageEvent, extra_args: dict[str, Any]) -> dict[str, Any]:
    payload = dict(extra_args)
    payload.setdefault("user_infos", [{"name": _sender_name(event), "gender": "unknown"}])
    return payload


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
    except MemeApiError as exc:
        await matcher.finish(str(exc))

    try:
        extra_args = _parse_extra_args(args.arg_items, args.args_json)
    except ValueError as exc:
        await matcher.finish(str(exc))

    images = _select_images(meme_info, await download_all_images_from_event(event, bot=bot))
    texts = _texts_from_args(meme_info, args.texts, args.explicit_texts)
    if message := _validate_counts(meme_info, images, texts):
        await matcher.finish(message)

    meme_args = _build_meme_args(event, extra_args)
    try:
        result = await _generate_meme(args.key, images=images, texts=texts, args=meme_args)
    except MemeApiError as exc:
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
    except MemeApiError as exc:
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
    except MemeApiError as exc:
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
