from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot

nonebot.init()


class BilibiliJobsTest(unittest.IsolatedAsyncioTestCase):
    async def test_manual_dynamic_check_sends_even_before_first_scheduled_check(self) -> None:
        from kanamibot.plugins.bilibili import jobs

        dynamic = {"id": 11, "name": "UP"}
        subscription = {"groups": [100], "dynamic": 10, "name": "Old UP"}
        sent = AsyncMock()
        saved = Mock()

        original_first_check = jobs.FIRST_DYNAMIC_CHECK
        self.addCleanup(setattr, jobs, "FIRST_DYNAMIC_CHECK", original_first_check)
        jobs.FIRST_DYNAMIC_CHECK = True
        with (
            patch.object(jobs, "get_credential", AsyncMock(return_value=object())),
            patch.object(
                jobs,
                "active_subscriptions",
                Mock(return_value={"1": subscription}),
            ),
            patch.object(jobs, "get_bot", Mock(return_value=object())),
            patch.object(jobs, "get_dynamic_by_uids", AsyncMock(return_value=[dynamic])),
            patch.object(jobs, "set_subscription", saved),
            patch.object(jobs, "parse_dynamic", Mock(return_value="message")),
            patch.object(jobs, "_send_group_dynamic", sent),
            patch.object(jobs, "cleanup_unsubscribed", Mock()),
        ):
            await jobs.check_bili_update(suppress_initial=False)

        saved.assert_called_once_with(
            "1",
            {"groups": [100], "dynamic": 11, "name": "UP"},
        )
        sent.assert_awaited_once()
        self.assertFalse(jobs.FIRST_DYNAMIC_CHECK)

    async def test_dynamic_delivery_failure_keeps_baseline_for_retry(self) -> None:
        from kanamibot.plugins.bilibili import jobs

        dynamic = {"id": 11, "name": "UP"}
        subscription = {"groups": [100], "dynamic": 10, "name": "Old UP"}
        saved = Mock()

        with (
            patch.object(jobs, "get_credential", AsyncMock(return_value=object())),
            patch.object(
                jobs,
                "active_subscriptions",
                Mock(return_value={"1": subscription}),
            ),
            patch.object(jobs, "get_bot", Mock(return_value=object())),
            patch.object(jobs, "get_dynamic_by_uids", AsyncMock(return_value=[dynamic])),
            patch.object(jobs, "set_subscription", saved),
            patch.object(jobs, "parse_dynamic", Mock(return_value="message")),
            patch.object(
                jobs,
                "_send_group_dynamic",
                AsyncMock(side_effect=RuntimeError("send failed")),
            ),
            patch.object(jobs, "cleanup_unsubscribed", Mock()),
        ):
            await jobs.check_bili_update(suppress_initial=False)

        saved.assert_not_called()

    async def test_live_delivery_failure_keeps_status_for_retry(self) -> None:
        from kanamibot.plugins.bilibili import jobs
        from kanamibot.plugins.bilibili.live import LiveStatus

        status = LiveStatus(
            uid=1,
            name="UP",
            live_status=1,
            title="直播中",
            cover="",
            url="https://live.bilibili.com/1",
        )
        subscription = {"groups": [100], "dynamic": 10, "live_status": 0, "name": "Old UP"}
        saved = Mock()

        original_first_check = jobs.FIRST_LIVE_CHECK
        self.addCleanup(setattr, jobs, "FIRST_LIVE_CHECK", original_first_check)
        jobs.FIRST_LIVE_CHECK = False
        with (
            patch.object(
                jobs,
                "active_subscriptions",
                Mock(return_value={"1": subscription}),
            ),
            patch.object(jobs, "get_bot", Mock(return_value=object())),
            patch.object(jobs, "query_live_statuses", AsyncMock(return_value={1: status})),
            patch.object(jobs, "set_subscription", saved),
            patch.object(jobs, "parse_live", Mock(return_value="message")),
            patch.object(
                jobs,
                "_send_group_message",
                AsyncMock(side_effect=RuntimeError("send failed")),
            ),
            patch.object(jobs, "cleanup_unsubscribed", Mock()),
        ):
            await jobs.check_live_update()

        saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
