from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.matcher import matchers
from nonebot.params import CommandArg
from nonebot.plugin import Plugin, PluginMetadata, get_loaded_plugins

from kanamibot.core.help_image import render_help_pages
from kanamibot.core.timing import timed

__plugin_meta__ = PluginMetadata(
    name="使用帮助",
    description="查看功能图片菜单，或按功能名称查找详细用法。",
    usage="help / 帮助 / 功能 / 菜单；help <功能名>",
)


@dataclass
class FeatureInfo:
    plugin_name: str
    title: str
    description: str = ""
    usage: str = ""
    module_name: str = ""
    entries: list[str] = field(default_factory=list)


# Only presentation copy lives here; command parsing and permissions stay in each plugin.
# (Readable name, what it does, a short starting point.)
GUIDES = {
    "help": ("使用帮助", "不知道发什么？先看菜单，再按功能名查用法。", "help 卡拉彼丘"),
    "strinova": (
        "卡拉彼丘战绩", "查询玩家资料、最近战绩和历史赛季。",
        "#卡丘战绩 昵称\n把「昵称」换成游戏名或玩家 ID。",
    ),
    "er_dak": (
        "永恒轮回", "查询玩家战绩、角色攻略、段位分数线，也能订阅群宠战绩。",
        "#BS战绩 昵称\n查角色：#BS 席琳 金魄（不写模式时查排位）",
    ),
    "arknights": (
        "明日方舟查询", "查询干员、材料、关卡和卫戍协议资料。",
        "#粥 银灰 技能\n#粥 帮助：查看更多查询方式。",
    ),
    "codex_gpt": (
        "AI 聊天与画图", "提问、聊天，或用文字描述生成图片。",
        "#gpt 你的问题\n#image 你想画的内容",
    ),
    "image": (
        "图库", "保存喜欢的图片，按图库或标签找图。",
        "回复一张图片发送：保存 图库名\n随机 图库名：从该图库抽一张。",
    ),
    "image_search": (
        "搜图", "用图片查找来源、作者或动画出处。",
        "回复要查的图片，发送 #搜图\n#搜图 帮助：查看更多用法。",
    ),
    "image_obfuscator": (
        "图片混淆", "把图片打乱，或把已混淆的图片还原。",
        "发送「混淆」或「解混淆」，再在 60 秒内发送图片。",
    ),
    "memes": (
        "制作表情包", "选择模板，配上文字或图片制作表情包。",
        "meme_list：先找模板\nmeme_info 模板名：查看该模板需要什么。",
    ),
    "bilibili": (
        "B站订阅", "订阅 UP 主，查看动态和直播提醒。",
        "关注 UP主名或UID\n关注列表：查看已经订阅的 UP 主。",
    ),
    "daily_report": (
        "群聊日报", "回顾今天聊了什么，查看群聊统计和词云。", "今日总结\n日报设置 查看",
    ),
    "group_manager": (
        "群管理", "设置头衔、名片或禁言；部分操作需要管理员权限。", "群管帮助",
    ),
    "core": (
        "功能开关", "管理本群启用的功能；修改开关需要相应管理权限。",
        "/list module：查看功能状态\n/enable 模块名：开启；/disable 模块名：关闭。",
    ),
    "majsoul": (
        "雀魂助手", "在 QQ 群内查询雀魂段位、牌谱和月报。",
        "qhpt 昵称：查段位分\nqhpaipu 昵称：查最近牌谱\nqh 123m：转换牌型",
    ),
    "image_classifier": (
        "图片归档（超管）", "自动整理收到的图片；查看归档仅供超管私聊使用。",
        "images：查看数量\n图ab：查看缩略图",
    ),
    "debug": ("调试开关（超管）", "查看或切换调试日志，仅超级管理员可用。", "#debug 状态"),
    "ping": ("连接测试", "检查机器人是否能正常回复。", "ping"),
    "echo": ("文字复读", "让机器人复述你输入的文字。", "echo 你好"),
    "skland": (
        "森空岛", "绑定账号后查询方舟、终末地角色，签到或查看抽卡记录。",
        "#sk 扫码登录：先绑定账号\n#sk 帮助：查看完整图片指南。",
    ),
    "nonebot_plugin_skland": (
        "森空岛", "绑定账号后查询方舟、终末地角色，签到或查看抽卡记录。",
        "#sk 扫码登录：先绑定账号\n#sk 帮助：查看完整图片指南。",
    ),
}

READING_GUIDE = (
    "1. 指令里的「昵称」「图库名」等要换成你自己的内容。\n"
    "2. <内容> 表示必须填写，[内容] 表示可以不填；输入时不用带括号。\n"
    "3. A / B 或 A | B 表示任选一种，不需要把分隔符一起发送。\n"
    "4. 指令和参数之间留空格；示例里的 # 或 / 要保留。\n"
    "5. 「回复图片」指先在 QQ 中引用那张图片，再发送指令。\n"
    "6. 功能是否可用取决于本群开关和你的权限；管理命令需要相应权限。"
)


def _guide(feature: FeatureInfo) -> tuple[str, str, str]:
    return GUIDES.get(feature.plugin_name, (feature.title, feature.description, ""))


def _plugin_title(plugin: Plugin) -> str:
    if plugin.metadata and plugin.metadata.name:
        return plugin.metadata.name
    return plugin.name


def _plugin_description(plugin: Plugin) -> str:
    if plugin.metadata and plugin.metadata.description:
        return plugin.metadata.description.strip()
    return ""


def _plugin_usage(plugin: Plugin) -> str:
    if plugin.metadata and plugin.metadata.usage:
        return plugin.metadata.usage.strip()
    return ""


def _is_library_plugin(plugin: Plugin) -> bool:
    return bool(plugin.metadata and plugin.metadata.type == "library")


def _command_to_text(command: tuple[str, ...]) -> str:
    return " ".join(part for part in command if part).strip()


def _shorten(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _format_regex(regex: str) -> str:
    return f"正则: {_shorten(regex)}"


def _call_entries(call: Any) -> list[str]:
    if hasattr(call, "cmds"):
        return [
            command_text
            for command in getattr(call, "cmds", ())
            if (command_text := _command_to_text(tuple(command)))
        ]

    if hasattr(call, "regex"):
        return [_format_regex(str(call.regex))]

    return []


def _matcher_entries(matcher: type[Matcher]) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()

    for checker in matcher.rule.checkers:
        for entry in _call_entries(checker.call):
            if entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)

    return entries


def _collect_matcher_entries() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    seen_by_plugin: dict[str, set[str]] = {}

    for priority in sorted(matchers):
        for matcher in matchers[priority]:
            plugin_name = getattr(matcher, "plugin_name", None)
            if not plugin_name:
                continue

            plugin_entries = result.setdefault(plugin_name, [])
            seen = seen_by_plugin.setdefault(plugin_name, set())

            for entry in _matcher_entries(matcher):
                if entry in seen:
                    continue
                seen.add(entry)
                plugin_entries.append(entry)

    return result


def collect_features() -> list[FeatureInfo]:
    matcher_entries = _collect_matcher_entries()
    features: list[FeatureInfo] = []

    for plugin in sorted(get_loaded_plugins(), key=lambda item: _plugin_title(item).casefold()):
        entries = matcher_entries.get(plugin.name, [])
        if _is_library_plugin(plugin) and not entries:
            continue

        features.append(
            FeatureInfo(
                plugin_name=plugin.name,
                title=_plugin_title(plugin),
                description=_plugin_description(plugin),
                usage=_plugin_usage(plugin),
                module_name=plugin.module_name,
                entries=entries,
            )
        )

    known_plugins = {feature.plugin_name for feature in features}
    for plugin_name, entries in sorted(matcher_entries.items()):
        if plugin_name in known_plugins:
            continue
        features.append(FeatureInfo(plugin_name=plugin_name, title=plugin_name, entries=entries))

    return features


def _matches_query(feature: FeatureInfo, query: str) -> bool:
    haystacks = {
        feature.plugin_name,
        feature.title,
        feature.module_name,
        feature.description,
        *_guide(feature),
        *feature.entries,
    }
    return any(query in value.casefold() for value in haystacks if value)


def _format_feature(feature: FeatureInfo, detailed: bool) -> str:
    title, description, start = _guide(feature)
    lines = [f"【{title}】", description]
    if start:
        lines.extend(["从这里开始：", start])
    if detailed and feature.usage:
        lines.extend(["完整用法：", feature.usage])
    elif not start:
        # Matcher regexes are implementation details, not usable instructions.
        entries = [entry for entry in feature.entries if not entry.startswith("正则:")]
        if entries:
            lines.append("可发送：" + " / ".join(entries[:4]))
        elif feature.usage:
            lines.append(feature.usage if detailed else feature.usage.splitlines()[0])
        else:
            lines.append("暂未提供使用说明，可向管理员询问。")
    if not detailed:
        lines.append(f"详细说明：help {title}")
    return "\n".join(line for line in lines if line)


def _help_content(query: str = "") -> tuple[str, list[tuple[str, str]]]:
    features = collect_features()
    normalized_query = query.strip().casefold()

    if normalized_query:
        features = [feature for feature in features if _matches_query(feature, normalized_query)]
        if not features:
            return "没有找到这个功能", [
                ("试试这样查找", f"没有找到与「{query.strip()}」匹配的功能。\n发送 help 查看菜单。")
            ]

    title = "KanamiBot 支持的功能"
    if normalized_query:
        title += f"：{query.strip()}"

    sections = [("第一次使用", READING_GUIDE)]
    for feature in features:
        label, _, body = _format_feature(feature, detailed=bool(normalized_query)).partition("\n")
        sections.append((label.strip("【】"), body))
    if not features:
        sections.append(("暂无功能", "暂时没有可展示的功能，请稍后再试。"))
    return title, sections


def build_help_text(query: str = "") -> str:
    title, sections = _help_content(query)
    return "\n\n".join([title, *(f"【{label}】\n{body}" for label, body in sections)])


def build_help_images(query: str = "") -> tuple[bytes, ...]:
    return render_help_pages(*_help_content(query))


help_command = on_command("help", aliases={"帮助", "功能", "菜单"}, priority=1, block=True)


@help_command.handle()
@timed()
async def handle_help(matcher: Matcher, args: Message = CommandArg()) -> None:  # noqa: B008
    query = args.extract_plain_text().strip()
    images = await asyncio.to_thread(build_help_images, query)
    for data in images:
        await matcher.send(MessageSegment.image(data))
    await matcher.finish()
