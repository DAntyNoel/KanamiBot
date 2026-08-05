from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from nonebot import get_driver, on_regex
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from kanamibot.core import ModuleRule
from kanamibot.core.paths import FILES_DIR

from .parser import ParsedCommand, parse_command, parse_match_args
from .store import get_binding, remove_binding, set_binding

MODULE_NAME = "er_dak"
COMMAND_PATTERN = r"^/?(?:er|永恒|永轮)(?:\s+.*)?$"
HELP_TEXT = """永恒轮回查询
/er 绑定 <昵称> | 解绑
/er 查询/段位/统计/近期/实验体/皮肤/队友 [昵称]
/er 战绩 [昵称] [1-10]
/er 多查 <昵称1> <昵称2> [昵称3]
/er 对比 <昵称1> <昵称2>
/er 最佳局/英雄池/出装习惯 [昵称]
/er 排行榜 [页]
/er 角色强度 <角色> [武器] | 物品 <名称> | 路线 <角色> [武器]
/er 来源"""

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
        HtmlCardRenderer,
        InvalidQuery,
        PlayerNotFound,
        RateLimited,
        RenderFailed,
        UpstreamUnavailable,
    )
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
        renderer: HtmlCardRenderer

    _runtime: _Runtime | None = None

    async def _get_runtime() -> _Runtime:
        global _runtime
        if _runtime is not None:
            return _runtime
        async with _runtime_lock:
            if _runtime is None:
                config = ERBSConfig(asset_directory=FILES_DIR / "erbs-assets")
                client = AsyncERBSClient(config)
                _runtime = _Runtime(client, ERBSService(client), HtmlCardRenderer(config))
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
            raise InvalidQuery("请提供昵称，或先使用 /er 绑定 <昵称>。")
        return nickname

    async def _card_for(
        service: ERBSService, event: MessageEvent, command: ParsedCommand
    ) -> Any:
        action, args = command.action, command.args
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
        raise InvalidQuery("未知子命令，请使用 /er 帮助。")

    @er_matcher.handle()
    async def handle_er(event: MessageEvent, matcher: Matcher) -> None:
        command = parse_command(event.get_plaintext())
        if command.action == "帮助":
            await matcher.finish(HELP_TEXT)
        if command.action == "来源":
            await matcher.finish("数据来源：DAK.GG（https://dak.gg/er）。缓存状态与数据更新时间会显示在图片底部。")
        if command.action == "绑定":
            if len(command.args) != 1:
                await matcher.finish("用法：/er 绑定 <DAK.GG 昵称>")
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
            card = await _card_for(runtime.service, event, command)
            image = await runtime.renderer.render(card)
        except PlayerNotFound:
            await matcher.finish("未找到该 DAK.GG 玩家，请检查昵称。")
        except RateLimited:
            await matcher.finish("DAK.GG 请求过于频繁，请稍后再试。")
        except (UpstreamUnavailable, AssetMissing):
            await matcher.finish("DAK.GG 数据或本地资源暂不可用，请稍后再试。")
        except RenderFailed as exc:
            await matcher.finish(f"图片渲染失败：{exc}")
        except (InvalidQuery, ValueError) as exc:
            await matcher.finish(str(exc))
        except Exception:
            logger.exception("[er_dak] unexpected command failure")
            await matcher.finish("永恒轮回查询暂时失败，请稍后再试。")
        await matcher.finish(MessageSegment.image(image))
