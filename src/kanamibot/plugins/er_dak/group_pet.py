"""Group tracking policy, independent of NoneBot and wall-clock scheduling."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from erbs_plugin import PlayerNotFound, RateLimited

from .pet_store import DAY, PetStore


@dataclass(frozen=True)
class Snapshot:
    nickname: str
    match_id: int | None = None
    started_at: float | None = None
    season: int = 0


def interval(started_at, now):
    if started_at is None:
        return 6 * 3600
    age = max(0, now - started_at)
    for limit, delay in ((7200, 120), (DAY, 600), (7 * DAY, 1800), (30 * DAY, 7200)):
        if age < limit:
            return delay
    return 6 * 3600


class PetTracker:
    def __init__(
        self,
        store: PetStore,
        source,
        render,
        send,
        enabled,
        *,
        clock=time.time,
        send_interval=6.1,
        subscription_source=None,
    ):
        self.store, self.source, self.render = store, source, render
        self.send, self.enabled, self.clock = send, enabled, clock
        self.subscription_source = subscription_source or source
        self.send_interval = send_interval
        self._tick_lock = asyncio.Lock()
        self._subscribe_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._last_send = 0.0

    async def subscribe(self, bot, group, requester, nickname):
        nickname = nickname.strip()
        if not nickname or len(nickname) > 64 or any(ord(c) < 32 for c in nickname):
            raise ValueError("请填写有效玩家昵称，含空格的昵称请加双引号。")
        async with self._subscribe_lock:
            rows = [
                r
                for r in self.store.rows(bot, group)
                if r["state"] == "active" and r["expires"] > self.clock()
            ]
            if any(r["player"] == nickname.casefold() for r in rows):
                return False
            if len(rows) >= 3:
                raise ValueError("群宠列表已满")
            snapshot = await asyncio.wait_for(self.subscription_source(nickname), 90)
            if not self.enabled(bot, group):
                raise ValueError("本群永恒轮回模块已关闭，暂时无法添加订阅。")
            now = self.clock()
            return self.store.add(
                bot,
                group,
                requester,
                snapshot,
                now,
                next_check=now + interval(snapshot.started_at, now),
            )

    async def _deliver(self, row, text, image=None, *, expiry=False):
        async with self._send_lock:
            delay = self.send_interval - (time.monotonic() - self._last_send)
            if delay > 0:
                await asyncio.sleep(delay)
            current = self.store.current(row)
            if not current or current["state"] != "active":
                return False
            if not self.enabled(row["bot"], row["group_id"]):
                return False
            if (current["expires"] <= self.clock()) != expiry:
                return False
            self._last_send = time.monotonic()
            await asyncio.wait_for(self.send(row["bot"], row["group_id"], text, image), 60)
            # Delivery cursors are committed only after a successful OneBot response.
            self.store.update(row, pushes=current["pushes"] + 1)
            return True

    async def tick(self, bot):
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            snapshots, images = {}, {}
            for row in sorted(self.store.rows(bot), key=lambda item: item["player"]):
                if row["state"] != "active" or row["next_check"] > self.clock():
                    continue
                if not self.enabled(bot, row["group_id"]):
                    self.store.update(row, next_check=self.clock() + 300)
                    continue
                phase = "查询战绩"
                try:
                    now = self.clock()
                    if row["expires"] <= now:
                        phase = "发送到期提醒"
                        if await self._deliver(
                            row,
                            f"已暂停追踪「{row['nickname']}」：7 天订阅已到期。"
                            "需要继续时，请使用 #BS 群宠 添加 玩家名 重新订阅。",
                            expiry=True,
                        ):
                            self.store.update(row, state="expired", error="", failures=0)
                        continue
                    key = row["player"]
                    if key not in snapshots:
                        # Process one player's groups together; retain at most one large PNG.
                        snapshots.clear()
                        images.clear()
                        try:
                            snapshots[key] = await asyncio.wait_for(
                                self.source(row["nickname"]), 90
                            )
                        except Exception as exc:
                            snapshots[key] = exc
                    snapshot = snapshots[key]
                    if isinstance(snapshot, Exception):
                        raise snapshot
                    if snapshot.nickname.casefold() != key:
                        raise ValueError("PlayerIdentityChanged")
                    latest = snapshot.match_id
                    updated = latest is not None and (
                        row["last_match"] is None or latest > row["last_match"]
                    )
                    consistent = latest is not None and latest == row["last_match"]
                    changes = {}
                    if updated:
                        phase = "生成结算图片"
                        if key not in images:
                            try:
                                images[key] = await asyncio.wait_for(self.render(snapshot), 90)
                            except Exception as exc:
                                images[key] = exc
                        image = images[key]
                        if isinstance(image, Exception):
                            raise image
                        phase = "发送对局"
                        if not await self._deliver(
                            row,
                            f"群宠「{snapshot.nickname}」刚刚完成了一场对局！",
                            image,
                        ):
                            continue
                        changes.update(last_match=latest, last_time=snapshot.started_at)
                    elif consistent and row["inactivity_match"] != latest:
                        start = snapshot.started_at
                        if start is not None and 0 < start <= now - 3 * DAY:
                            days, remainder = divmod(int(now - start), DAY)
                            hours, remainder = divmod(remainder, 3600)
                            phase = "发送未游玩提醒"
                            if not await self._deliver(
                                row,
                                f"「{snapshot.nickname}」已经 {days} 天 {hours} 小时 "
                                f"{remainder // 60} 分钟没有游玩了，可关注其近期状态。",
                            ):
                                continue
                            changes["inactivity_match"] = latest
                    start = snapshot.started_at if updated or consistent else row["last_time"]
                    self.store.update(
                        row,
                        **changes,
                        next_check=min(row["expires"], self.clock() + interval(start, now)),
                        failures=0,
                        error="",
                    )
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "群宠订阅：群 %s 的玩家「%s」%s失败，%s",
                        row["group_id"],
                        row["nickname"],
                        phase,
                        (
                            "上游接口限流，将自动延后重试"
                            if isinstance(exc, RateLimited)
                            else f"异常类型：{type(exc).__name__}"
                        ),
                    )
                    # Cancellation propagates; only an explicit lookup not-found stops tracking.
                    if phase == "查询战绩" and isinstance(exc, PlayerNotFound):
                        self.store.update(row, state="missing", error="上游确认玩家不存在")
                    else:
                        failures = min(20, row["failures"] + 1)
                        delay = max(
                            min(21600, 300 * 2 ** (failures - 1)),
                            interval(row["last_time"], self.clock()),
                        )
                        self.store.update(
                            row,
                            failures=failures,
                            error=f"{phase}："
                            + (
                                "上游限流，等待重试"
                                if isinstance(exc, RateLimited)
                                else "暂时失败，等待重试"
                            ),
                            next_check=(
                                min(row["expires"], self.clock() + delay)
                                if row["expires"] > self.clock()
                                else self.clock() + delay
                            ),
                        )
