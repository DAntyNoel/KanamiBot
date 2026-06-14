from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.log import logger


def _api_root() -> str:
    url_root = os.getenv("GPT_SOVITS_URL", "http://127.0.0.1").rstrip("/")
    port = os.getenv("GPT_SOVITS_PORT", "9550").strip()
    if port and ":" not in url_root.rsplit("/", 1)[-1]:
        return f"{url_root}:{port}"
    return url_root


API_ROOT = _api_root()
TTS_API_URL = f"{API_ROOT}/tts"
PROJECT_ROOT = os.getenv("GPT_SOVITS_PROJECT_ROOT")
PYTHON_EXEC = os.getenv("GPT_SOVITS_PYTHON_EXEC")
SERVER_SCRIPT = Path(__file__).with_name("tts_server.py")


class TTSQueueManager:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[Bot, object, str, str]] = asyncio.Queue()
        self.processing = False
        self.boot_lock = asyncio.Lock()

    async def add_task(self, bot: Bot, event: object, text: str, emotion: str = "natural") -> int:
        position = self.queue.qsize() + (1 if self.processing else 0)
        await self.queue.put((bot, event, text, emotion))
        if not self.processing:
            asyncio.create_task(self.worker())
        return position

    async def worker(self) -> None:
        self.processing = True
        logger.info("[gpt_sovits] TTS queue worker started.")

        while not self.queue.empty():
            bot, event, text, emotion = await self.queue.get()
            try:
                if not await self.ensure_backend_running(bot, event):
                    self.queue.task_done()
                    continue

                wav_bytes = await self.call_backend(text, emotion)
                if wav_bytes:
                    await bot.send(event, MessageSegment.record(wav_bytes))
                else:
                    await bot.send(event, "TTS 生成失败：后端返回空数据。")
            except Exception as exc:
                logger.exception("[gpt_sovits] TTS task failed: %s", exc)
                await bot.send(event, f"TTS 生成出错：{exc}")
            finally:
                self.queue.task_done()

        self.processing = False

    async def ensure_backend_running(self, bot: Bot, event: object) -> bool:
        if await self.check_health():
            return True

        async with self.boot_lock:
            if await self.check_health():
                return True

            project_root = Path(PROJECT_ROOT).resolve() if PROJECT_ROOT else None
            python_exec = Path(PYTHON_EXEC).resolve() if PYTHON_EXEC else None
            if project_root and python_exec is None:
                python_exec = project_root / "runtime" / "python.exe"

            if not project_root or not python_exec:
                await bot.send(
                    event,
                    "TTS 后端未运行，且未配置 GPT_SOVITS_PROJECT_ROOT/GPT_SOVITS_PYTHON_EXEC。",
                )
                return False
            if not python_exec.exists():
                await bot.send(event, f"找不到 GPT-SoVITS Python：{python_exec}")
                return False

            await bot.send(event, "检测到 TTS 后端未运行，正在启动 GPT-SoVITS 服务...")
            try:
                creationflags = 0
                if os.name == "nt" and os.getenv("GPT_SOVITS_SHOW_CONSOLE") != "1":
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    [str(python_exec), str(SERVER_SCRIPT)],
                    cwd=str(project_root),
                    creationflags=creationflags,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                logger.exception("[gpt_sovits] failed to start backend: %s", exc)
                await bot.send(event, f"TTS 后端启动失败：{exc}")
                return False

            for _ in range(20):
                await asyncio.sleep(3)
                if await self.check_health():
                    return True

            await bot.send(event, "TTS 后端启动超时，请检查 GPT-SoVITS 日志。")
            return False

    async def check_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(API_ROOT)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    async def call_backend(self, text: str, emotion: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(TTS_API_URL, json={"text": text, "emotion": emotion})
        if response.status_code != 200:
            raise RuntimeError(f"API Error Code: {response.status_code}")
        return response.content


tts_manager = TTSQueueManager()
