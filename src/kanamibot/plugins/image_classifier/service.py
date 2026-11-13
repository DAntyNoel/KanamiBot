from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import httpx
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.log import logger

from .classifier import RULES, Classification, ImageClassifier
from .config import Settings
from .inputs import DownloadFailureReporter, message_images
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
        self._results: OrderedDict[str, Classification] = OrderedDict()
        self._download_failures = DownloadFailureReporter()
        self._group_names: OrderedDict[tuple[str, int], tuple[float, str | None]] = OrderedDict()

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
        await asyncio.to_thread(self._restore_cache)
        self.accepting = True
        self.worker = asyncio.create_task(self._run(), name="group-image-classifier")
        logger.info("[image_classifier] CPU ONNX models ready; listening to group/private images")

    def _restore_cache(self) -> None:
        assert self.classifier is not None
        models = self.classifier.provenance()
        thresholds = RULES
        for _, record in self.store.rebuild_index():
            if len(self._results) >= self.settings.inference_cache_size:
                break
            if record.get("models") != models or record.get("thresholds") != thresholds:
                continue
            if record.get("inference_settings") != self._inference_settings():
                continue
            try:
                self._results.setdefault(
                    record["sha256"], Classification(**record["classification"])
                )
            except (TypeError, KeyError):
                continue

    def _inference_settings(self) -> dict:
        return {
            "animation_samples": self.settings.animation_samples,
            "max_pixels": self.settings.max_pixels,
        }

    async def submit(self, job: ImageJob) -> None:
        if self.accepting:
            # Backpressure instead of silently dropping images when the queue is full.
            await self.queue.put(job)

    async def _group_name(self, bot: Bot, group_id: int | None) -> str | None:
        if group_id is None:
            return None
        key = (str(bot.self_id), group_id)
        cached = self._group_names.get(key)
        if cached and cached[0] > time.monotonic():
            self._group_names.move_to_end(key)
            return cached[1]
        name = None
        try:
            async with asyncio.timeout(5):
                info = await bot.get_group_info(group_id=group_id)
            value = info.get("group_name")
            if isinstance(value, str) and value.strip():
                name = value.strip()
        except Exception as exc:
            logger.debug("[image_classifier] group {} name lookup failed ({})",
                         group_id, type(exc).__name__)
        # Cache failures briefly too, so unavailable APIs do not delay every image.
        self._group_names[key] = (time.monotonic() + (300 if name else 60), name)
        self._group_names.move_to_end(key)
        while len(self._group_names) > 1024:
            self._group_names.popitem(last=False)
        return name

    async def _run(self) -> None:
        assert self.client is not None
        while True:
            job = await self.queue.get()
            try:
                source = job.source | {
                    "group_name": await self._group_name(job.bot, job.source.get("group_id"))
                }
                async for data, details in message_images(
                    job.bot,
                    job.message,
                    self.client,
                    self.settings.max_download_mb * 1024 * 1024,
                    self.settings.local_roots,
                    self._download_failures,
                    source,
                ):
                    try:
                        await asyncio.to_thread(self._classify_and_save, data, source | details)
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
        if self.store.source_excluded(source):
            return
        started = time.perf_counter()
        digest = hashlib.sha256(data).hexdigest()
        result = self._results.get(digest)
        hit = result is not None
        if result is None:
            result = self.classifier.classify(data)
            self._results[digest] = result
        self._results.move_to_end(digest)
        while len(self._results) > self.settings.inference_cache_size:
            self._results.popitem(last=False)
        reference = Path(source["local_path"]) if source.get("local_path") else None
        self.store.save(
            data,
            result,
            source=source,
            models=self.classifier.provenance(),
            thresholds=RULES.copy(),
            reference=reference,
            inference_settings=self._inference_settings(),
        )
        logger.debug(
            "[image_classifier] inference+save {:.1f} ms; cache_hit={} local_reference={} queue={}",
            (time.perf_counter() - started) * 1000,
            hit,
            reference is not None,
            self.queue.qsize(),
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
