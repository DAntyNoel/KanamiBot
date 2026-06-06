from __future__ import annotations

import random
import re
from argparse import Namespace
from pathlib import Path
from typing import Annotated, Any

from nonebot import on_command, on_shell_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import ShellCommandArgs
from nonebot.plugin import PluginMetadata
from nonebot.rule import ArgumentParser

from kanamibot.core import get_first_superuser
from kanamibot.core.group_manager import ADMIN_PERMISSION, ModuleRule
from kanamibot.core.image_wrappers import (
    DATA_ROOT as IMAGE_FOLDER,
)
from kanamibot.core.image_wrappers import (
    create_image_gallery,
    delete_image,
    get_all_tags_with_imagedict,
    get_folder_name,
    get_image_file_path,
    get_imagedata,
    init_folder,
    save_image,
    similar_images,
    update_imagedata,
)
from kanamibot.core.utils.image import download_all_images_from_event, guess_extension
from kanamibot.core.utils.text import get_best_items_by_text_list

from .buffer import send_buffer

MODULE_NAME = "image"
GROUP_RULE = ModuleRule(MODULE_NAME)
IMAGE_PREVIEW_LIMIT = 5
ShellArgs = Annotated[Namespace, ShellCommandArgs()]

__plugin_meta__ = PluginMetadata(
    name="Image",
    description="图库、表情包保存、检索和维护命令。",
    usage=(
        "保存 <图库> [标签...] [-s]\n"
        "随机 <图库> / 所有 <图库> / 发送 <图库> <序号|标签>\n"
        "回复机器人发出的图片：信息、删除、标签 <tag...>、清空标签"
    ),
)


def _plain_tags(values: list[Any]) -> list[str]:
    tags: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            tags.append(text)
    return tags


def _message_id_from_receipt(receipt: Any) -> int | None:
    if isinstance(receipt, dict):
        raw_id = receipt.get("message_id")
    else:
        raw_id = receipt
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _reply_message_id(event: GroupMessageEvent) -> int | None:
    reply = getattr(event, "reply", None)
    raw_id = getattr(reply, "message_id", None) or getattr(reply, "id", None)
    if raw_id is not None:
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            pass

    for segment in event.message:
        if segment.type not in {"reply", "quote"}:
            continue
        raw_id = segment.data.get("id") or segment.data.get("message_id")
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            continue
    return None


def _image_path(folder: str, image_id: str, filename: str | None = None) -> Path:
    resolved = get_image_file_path(image_id, folder)
    if resolved:
        return resolved.resolve()
    if filename:
        return (IMAGE_FOLDER / folder / "files" / filename).resolve()
    return (IMAGE_FOLDER / folder / "files" / image_id).resolve()


async def _send_stored_image(
    matcher: type[Matcher],
    folder: str,
    image: dict[str, Any],
) -> None:
    image_id = str(image["id"])
    imagepath = _image_path(folder, image_id, str(image.get("filename", "")))
    receipt = await matcher.send(MessageSegment.image(imagepath))
    send_buffer.add(image_id=image_id, folder=folder, msg_id=_message_id_from_receipt(receipt))


def _format_image_info(image: dict[str, Any]) -> str:
    tags = ", ".join(image.get("tags", [])) or "无"
    contributor = image.get("contributor") or (image.get("group", 0), image.get("qq", 0))
    return (
        "图片信息：\n"
        f"ID: {image.get('id')}\n"
        f"图库: {image.get('folder')}\n"
        f"文件: {image.get('filename')}\n"
        f"标签: {tags}\n"
        f"贡献者: {contributor[1]} / 群 {contributor[0]}\n"
        f"描述: {image.get('description') or '无'}"
    )


save_parser = ArgumentParser()
save_parser.add_argument("folder", type=str, help="文件夹名称/别名")
save_parser.add_argument("tags", nargs="*", default=[], help="图片标签")
save_parser.add_argument("-s", "--sudo", action="store_true", help="跳过去重提示")

save_image_matcher = on_shell_command(
    "save_image",
    aliases={"保存", "存图", "收图"},
    parser=save_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@save_image_matcher.handle()
async def save_image_handler(
    bot: Bot,
    event: GroupMessageEvent,
    matcher: Matcher,
    args: ShellArgs,
) -> None:
    alias = args.folder
    tags = _plain_tags(args.tags)
    is_sudo = bool(args.sudo)

    first_superuser = await get_first_superuser()
    can_create_folder = first_superuser is not None and event.user_id == first_superuser

    existing_folder = get_folder_name(alias, create_new=False)
    new_folder_flag = False
    if not existing_folder:
        if not can_create_folder:
            await matcher.finish("该图库不存在，只有首个超管可以创建新图库。")
        new_folder_flag = True

    folder = get_folder_name(alias, create_new=new_folder_flag)
    if not folder:
        await matcher.finish("找不到指定图库。")

    images_bytes = await download_all_images_from_event(event, bot)
    if not images_bytes:
        await matcher.finish("请附带图片、回复图片或发送合并转发聊天记录进行保存。")

    saved_count = 0
    similar_messages: list[Message] = []

    for imagebytes in images_bytes:
        imagetype = guess_extension(imagebytes)
        if not is_sudo and not new_folder_flag:
            similar_imagedatas = similar_images(imagebytes, folder)
            if similar_imagedatas:
                ret_msg = Message()
                ret_msg += MessageSegment.at(event.user_id)
                ret_msg += MessageSegment.text(
                    f" 检测到相同图片。如确需保存，请使用 -s。\n例如：保存 {alias}"
                    f" {' '.join(tags)} -s\n"
                )
                ret_msg += MessageSegment.text("当前输入：\n")
                ret_msg += MessageSegment.image(imagebytes)
                ret_msg += MessageSegment.text(
                    f"tag: {','.join(tags) or '无'}\n=====================\n已保存图片：\n"
                )
                for sim_img in similar_imagedatas[:IMAGE_PREVIEW_LIMIT]:
                    path = _image_path(folder, sim_img["id"], sim_img.get("filename"))
                    ret_msg += MessageSegment.image(path)
                    ret_msg += MessageSegment.text(
                        f"{sim_img['filename']} tag: {','.join(sim_img['tags']) or '无'}\n"
                    )
                similar_messages.append(ret_msg)
                continue

        save_image(
            imagebytes,
            imagetype,
            folder,
            tags=tags,
            qq=event.user_id,
            group=event.group_id,
        )
        saved_count += 1

    if saved_count > 0:
        await matcher.send(f"小香同学收集了{saved_count}张图，谢谢你。")
    if similar_messages:
        await matcher.finish(similar_messages[0])
    if saved_count == 0:
        await matcher.finish("保存失败，请检查指令或使用 -s 跳过去重提示。")


pick_parser = ArgumentParser()
pick_parser.add_argument("folder", type=str, help="文件夹名称")

pick_image_matcher = on_shell_command(
    "pick_image",
    aliases={"随机", "来点"},
    parser=pick_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@pick_image_matcher.handle()
async def pick_image_handler(matcher: Matcher, args: ShellArgs) -> None:
    alias = args.folder
    folder = get_folder_name(alias)
    if not folder:
        await matcher.finish(f"找不到名为 {alias} 的图库。")

    images = init_folder(folder)["images"]
    if not images:
        await matcher.finish("这个图库里没有图片呢。")

    await _send_stored_image(pick_image_matcher, folder, random.choice(images))


list_parser = ArgumentParser()
list_parser.add_argument("folder", type=str, help="文件夹名称")

list_image_matcher = on_shell_command(
    "list_image",
    aliases={"所有"},
    parser=list_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
    permission=ADMIN_PERMISSION,
)


@list_image_matcher.handle()
async def list_image_handler(
    event: GroupMessageEvent,
    matcher: Matcher,
    args: ShellArgs,
) -> None:
    alias = args.folder
    folder = get_folder_name(alias)
    if not folder:
        await matcher.finish("图库不存在。")
    gallery = create_image_gallery(folder, event.group_id)
    if not gallery:
        await matcher.finish("这个图库里没有可见图片。")
    await matcher.finish(MessageSegment.image(gallery))


delete_image_matcher = on_command(
    "delete_image",
    aliases={"删除", "移除", "remove_image"},
    priority=10,
    block=True,
    rule=GROUP_RULE,
    permission=ADMIN_PERMISSION,
)


@delete_image_matcher.handle()
async def delete_image_handler(event: GroupMessageEvent, matcher: Matcher) -> None:
    quote_id = _reply_message_id(event)
    if quote_id is None:
        await matcher.finish("请回复要删除的图片消息。")
    request = send_buffer.get(quote_id)
    if not request:
        await matcher.finish("无法定位该图片的原始记录，可能已过期。")
    delete_image(*request)
    send_buffer.remove(quote_id)
    await matcher.finish("图片已删除。")


image_info_matcher = on_command(
    "image_info",
    aliases={"信息", "info"},
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@image_info_matcher.handle()
async def image_info_handler(event: GroupMessageEvent, matcher: Matcher) -> None:
    quote_id = _reply_message_id(event)
    if quote_id is None:
        await matcher.finish("请回复一张由机器人发送的图片来获取信息。")
    request = send_buffer.get(quote_id)
    if not request:
        await matcher.finish("无法定位该图片的原始记录，可能已过期。")
    imagedata = get_imagedata(*request)
    if not imagedata:
        await matcher.finish("图片信息不存在哦。")
    await matcher.finish(_format_image_info(imagedata))


tag_parser = ArgumentParser()
tag_parser.add_argument("tags", nargs="+", help="标签列表")

tag_matcher = on_shell_command(
    "tag",
    aliases={"标签", "打标", "TAG"},
    parser=tag_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
    permission=ADMIN_PERMISSION,
)


@tag_matcher.handle()
async def tag_handler(
    event: GroupMessageEvent,
    matcher: Matcher,
    args: ShellArgs,
) -> None:
    quote_id = _reply_message_id(event)
    if quote_id is None:
        await matcher.finish("请回复一张图片进行打标。")

    request = send_buffer.get(quote_id)
    if not request:
        await matcher.finish("无法获取图片上下文，请重试。")

    imagedata = get_imagedata(*request)
    if not imagedata:
        await matcher.finish("图片信息不存在哦。")

    old_tags = list(imagedata.get("tags", []))
    new_tags = _plain_tags(args.tags)
    merged_tags = list(dict.fromkeys([*old_tags, *new_tags]))
    update_imagedata(imagedata["id"], imagedata["folder"], tags=merged_tags)
    await matcher.finish(f"标签已添加: {','.join(new_tags)}")


untag_matcher = on_command(
    "untag",
    aliases={"清除tag", "删除tag", "清空标签"},
    priority=10,
    block=True,
    rule=GROUP_RULE,
    permission=ADMIN_PERMISSION,
)


@untag_matcher.handle()
async def untag_handler(event: GroupMessageEvent, matcher: Matcher) -> None:
    quote_id = _reply_message_id(event)
    if quote_id is None:
        await matcher.finish("请回复要清除标签的图片。")
    request = send_buffer.get(quote_id)
    if not request:
        await matcher.finish("无法获取图片上下文，请重试。")
    image_id, folder = request
    update_imagedata(image_id, folder, tags=[])
    await matcher.finish("标签已全部清除。")


select_parser = ArgumentParser()
select_parser.add_argument("folder", type=str, help="文件夹/图库")
select_parser.add_argument("query", nargs="+", help="序号或标签")

select_image_matcher = on_shell_command(
    "select_image",
    aliases={"选择", "发", "发送"},
    parser=select_parser,
    priority=10,
    block=True,
    rule=GROUP_RULE,
)


@select_image_matcher.handle()
async def select_image_handler(
    event: GroupMessageEvent,
    matcher: Matcher,
    args: ShellArgs,
) -> None:
    alias = args.folder
    query_str = " ".join(str(item) for item in args.query).strip()

    if re.match(r"^-?\d+$", query_str):
        folder = get_folder_name(alias)
        if not folder:
            await matcher.finish(f"找不到名为 {alias} 的图库。")
        images = init_folder(folder)["images"]
        total = len(images)
        if total == 0:
            await matcher.finish("这个图库里没有图片呢。")

        idx = int(query_str)
        if idx == 0 or idx > total or idx < -total:
            await matcher.finish(f"索引超出范围 (1-{total})。")
        real_idx = idx - 1 if idx > 0 else idx
        await _send_stored_image(select_image_matcher, folder, images[real_idx])
        return

    folder_scope = get_folder_name(alias)
    query = get_all_tags_with_imagedict(
        group_id=event.group_id,
        folder_name=folder_scope,
        force_load=True,
    )
    search_text = query_str if folder_scope else f"{alias} {query_str}"
    result = get_best_items_by_text_list(query, search_text)

    imagedicts: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, str]] = set()
    for image, _score in result:
        key = (str(image.get("folder")), str(image.get("id")))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        imagedicts.append(image)

    if not imagedicts:
        await matcher.finish("没有找到图片哦。")

    image = random.choice(imagedicts)
    await _send_stored_image(select_image_matcher, image["folder"], image)
