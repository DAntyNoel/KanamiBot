from __future__ import annotations

import asyncio
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any

from nonebot import get_driver, on_regex
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from kanamibot.core import ModuleRule
from kanamibot.core.paths import DEFAULT_FONT_PATH, FILES_DIR

from .parser import (
    COMMAND_PATTERN,
    DAK_ACTIONS,
    ParsedCommand,
    match_character,
    parse_command,
    parse_match_args,
)
from .progress import SummaryProgressReporter
from .store import get_binding, remove_binding, set_binding

MODULE_NAME = "er_dak"
HELP_TEXT = """永恒轮回查询
#永恒轮回战绩 <昵称> [全部/排位/匹配/钴协议]
#BS战绩 <昵称> [模式]
#BS排位 <昵称> | #BS钴协议 <昵称> | #BS匹配 <昵称>
#BS评价 <昵称>：使用 ERCraft 本赛季全部评价，在本地生成六维总结卡
#永恒轮回角色 <角色> [排位/钴协议] [段位]
#BS角色 <角色> [模式] [段位]
#BS <玩家昵称或角色名> [模式] [段位]
玩家查询默认展示全部页签；可用 #BS <昵称> 排位/钴协议/匹配 筛选，普通等同匹配。
命令头后必须有空格；自动查询优先角色，同名玩家请使用 #BS战绩。
含空格的名称请加双引号；角色支持名称、英文名、ID 和拼音。
#BS 绑定 <昵称> | 解绑
#BS 查询/段位/统计/近期/实验体/皮肤/队友 [昵称]
#BS 战绩 [昵称] [1-10]
#BS 多查 <昵称1> <昵称2> [昵称3] | 对比 <昵称1> <昵称2>
#BS 最佳局/英雄池/出装习惯/雷达 [昵称]
#BS 排行榜 [页] | 角色强度 <角色> [武器]
#BS 查询角色装备 <角色> [模式] [段位]
#BS 角色数据 [段位] | 段位统计/永恒多少分 [服务器]
#BS 物品 <名称/ID/拼音> | 路线 <角色> [武器]
#BS 帮助 | 来源"""

__plugin_meta__ = PluginMetadata(
    name="Eternal Return",
    description="DAK.GG 永恒轮回资料、战绩、分析与单图卡片。",
    usage=HELP_TEXT,
)

try:
    from erbs_plugin import (
        AssetMissing,
        AsyncERBSClient,
        ERBSConfig,
        ERBSService,
        InvalidQuery,
        PlayerNotFound,
        RateLimited,
        RenderFailed,
        SkiaCardRenderer,
        UpstreamUnavailable,
    )
    from erbs_plugin import (
        parse_command as parse_dak_command,
    )
    from erbs_plugin.cache import annotate_query_payload
except ImportError as exc:
    logger.warning(f"[er_dak] ERBS-plugin submodule/dependency unavailable; plugin disabled: {exc}")
else:
    _user_cooldowns: dict[int, float] = {}
    _group_cooldowns: dict[int, float] = {}
    _runtime_lock = asyncio.Lock()

    @dataclass(slots=True)
    class _Runtime:
        client: AsyncERBSClient
        service: ERBSService
        renderer: SkiaCardRenderer

    _runtime: _Runtime | None = None

    async def _get_runtime() -> _Runtime:
        global _runtime
        if _runtime is not None:
            return _runtime
        async with _runtime_lock:
            if _runtime is None:
                config = ERBSConfig(
                    asset_directory=FILES_DIR / "erbs-assets",
                    render_font_path=DEFAULT_FONT_PATH if DEFAULT_FONT_PATH.is_file() else None,
                )
                client = AsyncERBSClient(config)
                _runtime = _Runtime(client, ERBSService(client), SkiaCardRenderer(config))
        return _runtime

    async def _shutdown_runtime() -> None:
        global _runtime
        if _runtime is None:
            return
        await _runtime.renderer.close()
        await _runtime.client.aclose()
        _runtime = None

    get_driver().on_shutdown(_shutdown_runtime)

    er_matcher = on_regex(
        COMMAND_PATTERN,
        flags=re.IGNORECASE,
        rule=ModuleRule(MODULE_NAME),
        priority=5,
        block=True,
    )

    def _check_cooldown(event: MessageEvent) -> str | None:
        now = time.monotonic()
        user_wait = 5 - (now - _user_cooldowns.get(event.user_id, 0))
        if user_wait > 0:
            return f"查询冷却中，请 {user_wait:.1f} 秒后再试。"
        if isinstance(event, GroupMessageEvent):
            group_wait = 2 - (now - _group_cooldowns.get(event.group_id, 0))
            if group_wait > 0:
                return f"本群查询冷却中，请 {group_wait:.1f} 秒后再试。"
            _group_cooldowns[event.group_id] = now
        _user_cooldowns[event.user_id] = now
        return None

    def _nickname(event: MessageEvent, explicit: str | None) -> str:
        nickname = explicit or get_binding(event.user_id)
        if not nickname:
            raise InvalidQuery("请提供昵称，或先使用 #BS 绑定 <昵称>。")
        return nickname

    async def _card_for(
        service: ERBSService, event: MessageEvent, command: ParsedCommand, *, progress=None,
    ) -> Any:
        action, args = command.action, command.args
        if action == "summary":
            if len(args) != 1 or not args[0].strip():
                raise InvalidQuery("用法：#BS评价 <玩家昵称>；含空格的昵称请加双引号。")
            if progress is not None:
                return await service.published_player_summary(args[0], progress=progress)
            return await service.published_player_summary(args[0])
        if action == "自动查询":
            if not args or not args[0].strip():
                raise InvalidQuery("请输入玩家昵称或角色名，例如 #BS 玩家名 或 #BS 席琳。")
            metadata = await service.client.metadata("characters")
            character = match_character(args[0], metadata.get("characters") or ())
            action = "查询角色" if character is not None else "查询玩家"
            if character is not None:
                args = (character, *args[1:])
        if action in DAK_ACTIONS:
            if action in {"查询玩家", "查玩家"}:
                if len(args) == 1:
                    args = (*args, "全部")
                elif len(args) == 2 and args[1] == "匹配":
                    args = (args[0], "普通")
            query = parse_dak_command(shlex.join((action, *args)))
            if query.operation == "search":
                return await service.search_player_card(query.arguments[0], mode=query.mode)
            if query.operation in {"character", "character-equipment"}:
                return await service.character_analysis_card(
                    query.arguments[0],
                    mode=query.mode,
                    tier=query.tier,
                    equipment_only=query.operation == "character-equipment",
                )
            if query.operation == "character-stats":
                return await service.character_stats_card(tier=query.tier)
            if query.operation in {"tier-stats", "tier-cutoffs"}:
                return await service.tier_statistics_card(
                    server=query.server,
                    cutoffs_only=query.operation == "tier-cutoffs",
                )
            return await service.item_card(query.arguments[0])
        single = args[0] if args else None
        player_actions = {
            "查询",
            "段位",
            "统计",
            "近期",
            "实验体",
            "皮肤",
            "队友",
            "最佳局",
            "英雄池",
            "出装习惯",
            "雷达",
        }
        if action in player_actions:
            nickname = _nickname(event, single)
            methods = {
                "查询": service.player_overview,
                "段位": service.rank_card,
                "统计": service.stats_card,
                "近期": service.recent_card,
                "实验体": service.characters_card,
                "皮肤": service.skins_card,
                "队友": service.teammates_card,
                "最佳局": service.best_match_card,
                "英雄池": service.hero_pool_card,
                "出装习惯": service.equipment_card,
                "雷达": service.radar_card,
            }
            return await methods[action](nickname)
        if action == "战绩":
            explicit, count = parse_match_args(args)
            return await service.matches_card(_nickname(event, explicit), count=count)
        if action == "多查":
            binding = get_binding(event.user_id)
            names = list(args) or ([binding] if binding else [])
            return await service.multi_card([name for name in names if name])
        if action == "对比":
            if len(args) != 2:
                raise InvalidQuery("对比需要两个昵称。")
            return await service.compare_card(args[0], args[1])
        if action == "排行榜":
            page = int(args[0]) if args and args[0].isdigit() else 1
            if page < 1:
                raise InvalidQuery("排行榜页码必须大于 0。")
            return await service.leaderboard_card(page=page)
        if action in {"角色强度", "物品", "路线"}:
            if not args:
                raise InvalidQuery(f"{action}缺少查询名称。")
            if action == "角色强度":
                weapon = args[1] if len(args) > 1 else None
                return await service.character_card(args[0], weapon=weapon)
            if action == "物品":
                return await service.item_card(" ".join(args))
            return await service.routes_card(args[0], weapon=args[1] if len(args) > 1 else None)
        raise InvalidQuery("未知子命令，请使用 #BS 帮助。")

    @er_matcher.handle()
    async def handle_er(event: MessageEvent, matcher: Matcher) -> None:
        try:
            command = parse_command(event.get_plaintext())
        except ValueError as exc:
            await matcher.finish(str(exc))
        if command.action == "帮助":
            await matcher.finish(HELP_TEXT)
        if command.action == "来源":
            await matcher.finish(
                "数据来源：DAK.GG（https://dak.gg/er）；"
                "#BS评价 使用 ERCraft（https://ercraft.net）返回的评价，在本地生成图片。"
            )
        if command.action == "绑定":
            if len(command.args) != 1:
                await matcher.finish("用法：#BS 绑定 <DAK.GG 昵称>")
            set_binding(event.user_id, command.args[0])
            await matcher.finish(f"已绑定永恒轮回昵称：{command.args[0]}")
        if command.action == "解绑":
            removed = remove_binding(event.user_id)
            await matcher.finish("已解绑。" if removed else "当前没有绑定昵称。")
        cooldown = _check_cooldown(event)
        if cooldown:
            await matcher.finish(cooldown)
        try:
            runtime = await _get_runtime()
            card = await _card_for(
                runtime.service, event, command, progress=SummaryProgressReporter(matcher.send),
            )
            card = annotate_query_payload(card)
            image = await runtime.renderer.render(card)
        except PlayerNotFound:
            source = "ERCraft" if command.action == "summary" else "DAK.GG"
            await matcher.finish(f"未找到该 {source} 玩家，请检查昵称。")
        except RateLimited:
            source = "ERCraft" if command.action == "summary" else "DAK.GG"
            await matcher.finish(f"{source} 请求过于频繁，请稍后再试。")
        except UpstreamUnavailable as exc:
            if command.action == "summary":
                await matcher.finish(f"ERCraft 查询失败：{exc}")
            await matcher.finish("DAK.GG 数据或本地资源暂不可用，请稍后再试。")
        except AssetMissing as exc:
            if command.action == "summary":
                await matcher.finish(f"评价卡本地资源暂不可用：{exc}")
            await matcher.finish("DAK.GG 数据或本地资源暂不可用，请稍后再试。")
        except RenderFailed as exc:
            await matcher.finish(f"图片渲染失败：{exc}")
        except (InvalidQuery, ValueError) as exc:
            await matcher.finish(str(exc).replace("/帮助", "#BS 帮助"))
        except Exception:
            logger.exception("[er_dak] unexpected command failure")
            await matcher.finish("永恒轮回查询暂时失败，请稍后再试。")
        await matcher.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))
