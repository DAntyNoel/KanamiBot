from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.log import logger

from .classifier import ImageClassifier
from .config import Settings
from .inputs import message_images
from .storage import ResultStore


@dataclass(frozen=True)
class ImageJob:
    bot: Bot
    message: Message
    source: dict


class ClassificationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ResultStore(settings.cache_dir)
        self.queue: asyncio.Queue[ImageJob] = asyncio.Queue(maxsize=settings.queue_size)
        self.classifier: ImageClassifier | None = None
        self.worker: asyncio.Task | None = None
        self.client: httpx.AsyncClient | None = None
        self.accepting = False

    async def start(self) -> None:
        # Even a missing/broken model bundle must not stop retention cleanup.
        try:
            await asyncio.to_thread(self.store.cleanup)
        except Exception:
            logger.exception("[image_classifier] startup retention cleanup failed")
        if not self.settings.enabled:
            return
        try:
            self.classifier = await asyncio.to_thread(ImageClassifier, self.settings)
        except Exception:
            logger.exception(
                "[image_classifier] models could not be loaded from {}; prepare them with "
                "scripts/export_image_classifier_models.py and restart the bot",
                self.settings.model_dir,
            )
            return
        self.client = httpx.AsyncClient(follow_redirects=True, timeout=30)
        self.accepting = True
        self.worker = asyncio.create_task(self._run(), name="group-image-classifier")
        logger.info("[image_classifier] CPU ONNX models ready; listening to all groups")

    async def submit(self, job: ImageJob) -> None:
        if self.accepting:
            # Backpressure instead of silently dropping images when the queue is full.
            await self.queue.put(job)

    async def _run(self) -> None:
        assert self.client is not None
        while True:
            job = await self.queue.get()
            try:
                async for data, details in message_images(
                    job.bot,
                    job.message,
                    self.client,
                    self.settings.max_download_mb * 1024 * 1024,
                ):
                    try:
                        await asyncio.to_thread(self._classify_and_save, data, job.source | details)
                    except Exception:
                        logger.exception(
                            "[image_classifier] failed image: group {} message {} image {}",
                            job.source["group_id"],
                            job.source["message_id"],
                            details["image_index"],
                        )
            except Exception:
                logger.exception("[image_classifier] message processing failed")
            finally:
                self.queue.task_done()

    def _classify_and_save(self, data: bytes, source: dict) -> None:
        assert self.classifier is not None
        result = self.classifier.classify(data)
        self.store.save(
            data,
            result,
            source=source,
            models=self.classifier.provenance(),
            thresholds={
                "anime": self.settings.anime_threshold,
                "nsfw": self.settings.nsfw_threshold,
            },
        )

    async def stop(self) -> None:
        self.accepting = False
        if self.worker is not None:
            try:
                await asyncio.wait_for(self.queue.join(), timeout=30)
            except TimeoutError:
                logger.warning(
                    "[image_classifier] shutdown timed out; {} queued messages remain in memory",
                    self.queue.qsize(),
                )
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        if self.client is not None:
            await self.client.aclose()
            self.client = None
