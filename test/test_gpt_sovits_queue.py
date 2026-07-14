from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock


class TTSQueueManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_tasks_share_one_worker(self) -> None:
        from kanamibot.plugins.gpt_sovits.tts_queue import TTSQueueManager

        manager = TTSQueueManager()
        manager.ensure_backend_running = AsyncMock(return_value=False)
        bot = AsyncMock()

        await manager.add_task(bot, object(), "任务一")
        worker_task = manager.worker_task
        await manager.add_task(bot, object(), "任务二")

        self.assertIs(manager.worker_task, worker_task)
        await asyncio.wait_for(manager.queue.join(), timeout=1)

        worker_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_task

    async def test_unavailable_backend_finishes_task_without_sticking_queue(self) -> None:
        from kanamibot.plugins.gpt_sovits.tts_queue import TTSQueueManager

        manager = TTSQueueManager()
        manager.ensure_backend_running = AsyncMock(return_value=False)
        bot = AsyncMock()

        await manager.add_task(bot, object(), "测试", emotion="natural")
        await asyncio.wait_for(manager.queue.join(), timeout=1)

        self.assertFalse(manager.processing)
        self.assertEqual(manager.queue._unfinished_tasks, 0)
        self.assertIsNotNone(manager.worker_task)

        manager.worker_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await manager.worker_task


if __name__ == "__main__":
    unittest.main()
