from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.internal.matcher import Matcher
from nonebot.matcher import matchers
from nonebot.params import CommandArg
from nonebot.plugin import Plugin, PluginMetadata, get_loaded_plugins

__plugin_meta__ = PluginMetadata(
    name="Help",
    description="动态列出当前已加载插件和命令入口。",
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
    }
    return any(query in value.casefold() for value in haystacks if value)


def _format_feature(feature: FeatureInfo, detailed: bool) -> str:
    lines = [f"【{feature.title}】"]
    if feature.description:
        lines.append(feature.description)

    if feature.entries:
        lines.append("入口：" + " / ".join(feature.entries))

    if detailed and feature.usage:
        lines.append("用法：")
        lines.extend(feature.usage.splitlines())
    elif feature.usage and not feature.entries:
        lines.append("用法：" + feature.usage.replace("\n", " / "))

    return "\n".join(lines)


def build_help_text(query: str = "") -> str:
    features = collect_features()
    normalized_query = query.strip().casefold()

    if normalized_query:
        features = [feature for feature in features if _matches_query(feature, normalized_query)]
        if not features:
            return f"没有找到与「{query.strip()}」匹配的功能。"

    title = "KanamiBot 支持的功能"
    if normalized_query:
        title += f"：{query.strip()}"

    lines = [title]
    lines.extend(_format_feature(feature, detailed=bool(normalized_query)) for feature in features)
    return "\n\n".join(lines)


help_command = on_command("help", aliases={"帮助", "功能", "菜单"}, priority=1, block=True)


@help_command.handle()
async def handle_help(matcher: Matcher, args: Message = CommandArg()) -> None:  # noqa: B008
    query = args.extract_plain_text().strip()
    await matcher.finish(build_help_text(query))
