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


if __name__ == "__main__":
    unittest.main()
