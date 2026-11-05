from __future__ import annotations

import io
import json
import tempfile
import unittest
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import nonebot
import numpy as np
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from PIL import Image

nonebot.init(image_classifier_enabled=False)

from kanamibot.plugins.image_classifier.classifier import (  # noqa: E402
    ImageClassifier,
    summarize,
)
from kanamibot.plugins.image_classifier.config import Settings  # noqa: E402
from kanamibot.plugins.image_classifier.inputs import download_image, message_images  # noqa: E402
from kanamibot.plugins.image_classifier.models import SPECS, OnnxModel, preprocess  # noqa: E402
from kanamibot.plugins.image_classifier.service import (  # noqa: E402
    ClassificationService,
    ImageJob,
)
from kanamibot.plugins.image_classifier.storage import ResultStore  # noqa: E402

ANIME = dict(zip(SPECS[0].labels, (0.2, 0.2, 0.2, 0.4), strict=True))
NORMAL = {"normal": 0.95, "nsfw": 0.05}


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def fake_classifier(settings: Settings | None = None) -> ImageClassifier:
    classifier = ImageClassifier.__new__(ImageClassifier)
    classifier.settings = settings or Settings()
    classifier.anime = Mock()
    classifier.anime.predict.return_value = ANIME
    classifier.nsfw = Mock()
    classifier.nsfw.predict.return_value = NORMAL
    classifier.anime_real = None
    return classifier


class ClassificationTest(unittest.TestCase):
    def test_unload_releases_model_and_reload_is_idempotent(self) -> None:
        classifier = fake_classifier()
        reference = weakref.ref(classifier.anime)
        classifier.unload_model("anime")
        classifier.unload_model("anime")
        self.assertIsNone(reference())
        self.assertEqual(classifier.available_models(), ("nsfw",))
        with self.assertRaises(ValueError):
            classifier.classify(png_bytes(), ("anime",))
        with patch("kanamibot.plugins.image_classifier.classifier.OnnxModel") as load:
            classifier.load_model("anime")
            classifier.load_model("anime")
        load.assert_called_once_with(classifier.settings.model_dir, SPECS[0], 2)
        self.assertIs(classifier.anime, load.return_value)

    def test_lifecycle_rejects_unknown_and_startup_disabled_models(self) -> None:
        classifier = fake_classifier(Settings(anime_real_enabled=False))
        with patch("kanamibot.plugins.image_classifier.classifier.OnnxModel") as load:
            for key in ("bad-key", "anime_real"):
                with self.assertRaises(ValueError):
                    classifier.load_model(key)
            with self.assertRaises(ValueError):
                classifier.unload_model("bad-key")
        load.assert_not_called()

    def test_failed_reload_keeps_model_unloaded_and_other_models_intact(self) -> None:
        classifier = fake_classifier()
        nsfw = classifier.nsfw
        classifier.unload_model("anime")
        with (
            patch(
                "kanamibot.plugins.image_classifier.classifier.OnnxModel",
                side_effect=FileNotFoundError("missing model"),
            ),
            self.assertRaises(FileNotFoundError),
        ):
            classifier.load_model("anime")
        self.assertIsNone(classifier.anime)
        self.assertIs(classifier.nsfw, nsfw)

    def test_deepghs_only_skips_other_models_and_keeps_safety_unknown(self) -> None:
        classifier = fake_classifier()
        classifier.anime_real = Mock()
        classifier.anime_real.predict.return_value = {"anime": 0.9, "real": 0.1}
        result = classifier.classify(png_bytes(), ("anime_real",))
        classifier.anime.predict.assert_not_called()
        classifier.nsfw.predict.assert_not_called()
        classifier.anime_real.predict.assert_called_once()
        self.assertEqual(classifier.anime_real.predict.call_args.args[0].shape, (1, 3, 384, 384))
        self.assertEqual(result.dimension, "anime")
        self.assertEqual(result.dimension_model, "anime_real")
        self.assertEqual(result.subtype, "unspecified")
        self.assertEqual(result.anime_scores, {})
        self.assertEqual(result.nsfw_scores, {})
        self.assertIsNone(result.nsfw_probability)
        self.assertEqual(result.safety, "unknown")

    def test_nsfw_only_does_not_invent_style_results(self) -> None:
        classifier = fake_classifier()
        result = classifier.classify(png_bytes(), ("nsfw",))
        self.assertEqual(result.dimension, "unknown")
        self.assertIsNone(result.anime_probability)
        self.assertEqual(result.anime_scores, {})
        classifier.anime.predict.assert_not_called()
        self.assertEqual(result.safety, "unknown")
        self.assertIsNone(result.category_b)

    def test_both_style_models_keep_independent_results_without_averaging(self) -> None:
        classifier = fake_classifier()
        classifier.anime_real = Mock()
        classifier.anime_real.predict.return_value = {"anime": 0.01, "real": 0.99}
        result = classifier.classify(png_bytes())
        self.assertAlmostEqual(result.anime_probability, 0.6)
        self.assertEqual(result.anime_real_scores, {"anime": 0.01, "real": 0.99})
        self.assertEqual(result.dimension_model, "anime+anime_real")
        self.assertEqual(result.category_a, "A2")
        self.assertEqual(set(result.enabled_models), {"anime", "anime_real", "nsfw"})

    def test_disabled_model_is_not_loaded(self) -> None:
        with patch("kanamibot.plugins.image_classifier.classifier.OnnxModel") as load:
            classifier = ImageClassifier(Settings(anime_enabled=False, nsfw_enabled=False))
        self.assertEqual(load.call_count, 1)
        self.assertEqual(load.call_args.args[1].key, "anime_real")
        self.assertEqual(classifier.available_models(), ("anime_real",))

    def test_empty_or_unavailable_selection_never_reaches_inference(self) -> None:
        classifier = fake_classifier()
        for selected in [(), ("anime_real",), ("bad-key",)]:
            with self.assertRaises(ValueError):
                classifier.classify(png_bytes(), selected)
        classifier.anime.predict.assert_not_called()
        classifier.nsfw.predict.assert_not_called()

    def test_deepghs_preprocessing_matches_upstream_white_background_pipeline(self) -> None:
        image = Image.new("RGBA", (13, 7), (20, 88, 190, 128))
        background = Image.new("RGBA", image.size, "white")
        background.paste(image, (0, 0), mask=image)
        resized = background.convert("RGB").resize((384, 384), Image.Resampling.BILINEAR)
        reference = (np.asarray(resized).transpose(2, 0, 1) / 255.0).astype(np.float32)
        reference = ((reference - np.asarray([0.5])[:, None, None]) / 0.5).astype(np.float32)
        np.testing.assert_array_equal(preprocess(image, SPECS[2]), reference[None])
        transparent = preprocess(Image.new("RGBA", (4, 4), (255, 0, 0, 0)), SPECS[2])
        np.testing.assert_array_equal(transparent, np.ones((1, 3, 384, 384), dtype=np.float32))

    def test_deepghs_probabilities_are_not_softmaxed_twice(self) -> None:
        model = OnnxModel.__new__(OnnxModel)
        model.spec = SPECS[2]
        model.session = Mock()
        model.session.run.return_value = [np.asarray([[0.98, 0.02]], dtype=np.float32)]
        result = model.predict(np.zeros((1, 3, 384, 384), dtype=np.float32))
        self.assertAlmostEqual(result["anime"], 0.98)
        self.assertAlmostEqual(result["real"], 0.02)

    def test_medium_anime_probability_is_undetermined(self) -> None:
        result = summarize(ANIME, NORMAL, Settings())
        self.assertEqual(result["dimension"], "unknown")
        self.assertEqual(result["category_a"], "A3")
        self.assertEqual(result["original_label"], "non_anime")
        self.assertAlmostEqual(result["anime_probability"], 0.6)
        self.assertEqual(result["subtype"], "unspecified")
        self.assertEqual(result["safety"], "normal")
        scores = {"normal": 0.2, "nsfw": 0.8}
        self.assertEqual(summarize(ANIME, scores, Settings())["safety"], "nsfw")

    def test_a_boundaries_either_model_and_a1_priority(self) -> None:
        for fine, binary, expected in [
            (0.7, 0.7, "A3"),
            (0.3, 0.5, "A2"),
            (0.5, 0.3, "A2"),
            (0.70001, 0.2, "A1"),
            (0.2, 0.70001, "A1"),
            (0.30001, 0.69999, "A3"),
            (None, None, "A3"),
            (None, 0.8, "A1"),
            (0.2, None, "A2"),
        ]:
            with self.subTest(fine=fine, binary=binary):
                scores = (
                    {}
                    if fine is None
                    else {
                        "anime_adult": 0,
                        "anime_contentious": 0,
                        "anime_non_adult": fine,
                        "non_anime": 1 - fine,
                    }
                )
                result = summarize(
                    scores, NORMAL, Settings(), {} if binary is None else {"anime": binary}
                )
                self.assertEqual(result["category_a"], expected)

    def test_b_boundaries_or_conditions_and_priority(self) -> None:
        for adult, contentious, nsfw, expected in [
            (0.5, 0.2, 0.2, "B3"),
            (0.50001, 0, 0.0, "B1"),
            (0, 0, 0.70001, "B1"),
            (0, 0, 0.7, "B2"),
            (0, 0, 0.20001, "B2"),
            (0.4, 0.30001, 0, "B2"),
            (0.6, 0.2, 0.3, "B1"),
            (0.1, 0.1, 0.1, "B3"),
        ]:
            with self.subTest(adult=adult, contentious=contentious, nsfw=nsfw):
                result = summarize(
                    {
                        "anime_adult": adult,
                        "anime_contentious": contentious,
                        "anime_non_adult": 0,
                        "non_anime": 1 - adult - contentious,
                    },
                    {"nsfw": nsfw, "normal": 1 - nsfw},
                    Settings(),
                )
                self.assertEqual(result["category_b"], expected)
        self.assertEqual(summarize({}, {"nsfw": 0.8}, Settings())["category_b"], "B1")
        self.assertIsNone(summarize({}, {"nsfw": 0.1}, Settings())["category_b"])

    def test_vit_preprocessing_layout_and_normalization(self) -> None:
        pixels = preprocess(Image.new("RGBA", (10, 20), (255, 0, 0, 255)))
        self.assertEqual(pixels.shape, (1, 3, 224, 224))
        self.assertEqual(pixels.dtype, np.float32)
        np.testing.assert_array_equal(pixels[0, :, 0, 0], [1, -1, -1])
        self.assertTrue(pixels.flags.c_contiguous)

    def test_animated_image_uses_highest_sampled_nsfw_probability(self) -> None:
        buffer = io.BytesIO()
        images = [Image.new("RGB", (8, 8), color) for color in ("red", "blue", "green")]
        images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:])
        classifier = fake_classifier(Settings(animation_samples=2))
        classifier.nsfw.predict.side_effect = [NORMAL, {"normal": 0.1, "nsfw": 0.9}]
        result = classifier.classify(buffer.getvalue())
        self.assertEqual([frame["index"] for frame in result.frames], [0, 2])
        self.assertEqual(result.total_frames, 3)
        self.assertEqual(result.safety, "nsfw")
        self.assertAlmostEqual(result.nsfw_probability, 0.9)
        self.assertEqual(classifier.anime.predict.call_count, 2)

    def test_image_pixel_and_byte_limits(self) -> None:
        classifier = fake_classifier(Settings(max_pixels=10))
        with self.assertRaisesRegex(ValueError, "pixel limit"):
            classifier.classify(png_bytes())
        classifier.anime.predict.assert_not_called()
        classifier = fake_classifier(Settings(max_download_mb=1))
        with self.assertRaisesRegex(ValueError, "byte limit"):
            classifier.classify(b"x" * (1024 * 1024 + 1))

    def test_corrupt_image_does_not_reach_inference(self) -> None:
        classifier = fake_classifier()
        with self.assertRaises(OSError):
            classifier.classify(b"not an image")
        classifier.anime.predict.assert_not_called()

    def test_model_checksum_is_verified_before_session_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "anime"
            folder.mkdir()
            (folder / "manifest.json").write_text(
                json.dumps(
                    {
                        "repository": SPECS[0].repository,
                        "revision": SPECS[0].revision,
                        "labels": list(SPECS[0].labels),
                        "sha256": "wrong",
                        "preprocessing": "vit-224-bilinear-rescale-normalize-0.5",
                    }
                )
            )
            (folder / "model.onnx").write_bytes(b"modified")
            with patch("kanamibot.plugins.image_classifier.models.ort.InferenceSession") as session:
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    OnnxModel(Path(tmp), SPECS[0])
                session.assert_not_called()


class ResultStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ResultStore(Path(self.temp.name) / "results")
        self.now = datetime(2026, 11, 4, 4, tzinfo=UTC)
        self.data = png_bytes()
        self.result = fake_classifier().classify(self.data)

    def save(self, when: datetime) -> Path:
        return self.store.save(
            self.data,
            self.result,
            source={"group_id": 123},
            models={"version": "pinned"},
            thresholds={"anime": 0.5, "nsfw": 0.5},
            now=when,
        )

    def test_save_preserves_bytes_scores_and_source(self) -> None:
        path = self.save(self.now)
        record = json.loads(path.read_text())
        self.assertEqual((path.parent / record["image_file"]).read_bytes(), self.data)
        self.assertEqual(record["source"], {"group_id": 123})
        self.assertEqual(record["classification"]["anime_scores"], ANIME)
        self.assertEqual(path.parent.parent.name, "normal")
        self.assertEqual(path.parent.parent.parent.name, "unspecified")
        self.assertEqual(datetime.fromisoformat(record["expires_at"]), self.now + timedelta(days=2))

    def test_cleanup_48h_boundary_and_corrupt_metadata(self) -> None:
        expired = self.save(self.now - timedelta(days=2))
        fresh = self.save(self.now - timedelta(days=2) + timedelta(microseconds=1))
        older = self.save(self.now - timedelta(days=4))
        older.write_text("corrupt JSON")
        foreign = older.parent.parent / "unrelated"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("keep")
        self.assertEqual(self.store.cleanup(now=self.now), 2)
        self.assertFalse(expired.exists())
        self.assertFalse(older.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue((foreign / "keep.txt").exists())
        self.assertEqual(self.store.cleanup(now=self.now), 0)

    def test_duplicate_content_has_separate_occurrence_and_does_not_extend_retention(self) -> None:
        expired = self.save(self.now - timedelta(days=4))
        fresh = self.save(self.now)
        self.assertNotEqual(expired, fresh)
        self.store.cleanup(now=self.now)
        self.assertFalse(expired.exists())
        self.assertTrue(fresh.exists())

    def test_disabled_model_categories_are_also_cleaned(self) -> None:
        from dataclasses import replace

        for dimension, subtype in [("anime", "unspecified"), ("unknown", "unspecified")]:
            result = replace(self.result, dimension=dimension, subtype=subtype, safety="unknown")
            self.store.save(
                self.data,
                result,
                source={},
                models={},
                thresholds={},
                now=self.now - timedelta(days=4),
            )
        self.assertEqual(self.store.cleanup(now=self.now), 2)

    def test_cleanup_ignores_symlink_to_other_directory(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep")
        path = self.save(self.now - timedelta(days=4))
        import shutil

        shutil.rmtree(path.parent)
        try:
            path.parent.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows account lacks symlink privilege")
            raise
        self.assertEqual(self.store.cleanup(now=self.now), 0)
        self.assertTrue(marker.exists())


class ImageInputsTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_limit_without_content_length(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaisesRegex(ValueError, "download limit"):
                await download_image(client, "https://example.test/image", 4)

    async def test_images_forward_cycle_and_failed_image_are_isolated(self) -> None:
        bot = Mock()
        bot.get_forward_msg = AsyncMock(
            return_value={
                "messages": [
                    {
                        "content": [
                            {"type": "image", "data": {"url": "https://example.test/ok"}},
                            {"type": "forward", "data": {"id": "loop"}},
                        ]
                    },
                ]
            }
        )
        bot.get_image = AsyncMock(return_value={"url": "https://example.test/ok"})

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404 if request.url.path == "/bad" else 200, content=png_bytes())

        message = Message(
            [
                MessageSegment.image("https://example.test/bad"),
                MessageSegment.image("qq-image-file"),
                MessageSegment("forward", {"id": "loop"}),
                MessageSegment.image("https://example.test/ok"),
            ]
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            items = [item async for item in message_images(bot, message, client, 100_000)]
        self.assertEqual(len(items), 3)
        self.assertEqual([item[1]["image_index"] for item in items], [2, 3, 4])
        self.assertEqual([item[1]["forwarded"] for item in items], [False, True, False])
        bot.get_forward_msg.assert_awaited_once_with(id="loop")

    async def test_quote_alone_does_not_download_old_images(self) -> None:
        bot = Mock()
        async with httpx.AsyncClient() as client:
            items = [
                item
                async for item in message_images(
                    bot,
                    Message(MessageSegment.reply(123)),
                    client,
                    100,
                )
            ]
        self.assertEqual(items, [])


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_continues_after_failed_inference_and_drains_on_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ClassificationService(Settings(cache_dir=Path(tmp), queue_size=1))
            classifier = fake_classifier()
            classifier.provenance = Mock(return_value={})
            good_classify = classifier.classify
            classifier.classify = Mock(
                side_effect=[ValueError("bad image"), good_classify(png_bytes())]
            )

            async def fake_images(*_args):
                yield png_bytes(), {"image_index": 1}

            with (
                patch(
                    "kanamibot.plugins.image_classifier.service.ImageClassifier",
                    return_value=classifier,
                ),
                patch("kanamibot.plugins.image_classifier.service.message_images", fake_images),
                patch("kanamibot.plugins.image_classifier.service.logger") as log,
            ):
                await service.start()
                job = ImageJob(Mock(), Message(), {"group_id": 1, "message_id": 2})
                await service.submit(job)
                await service.submit(job)
                await service.stop()
            log.exception.assert_called_once()
            self.assertEqual(classifier.classify.call_count, 2)
            self.assertEqual(len(list(Path(tmp).rglob("result.json"))), 1)
            self.assertFalse(service.accepting)

    async def test_missing_models_do_not_start_worker_or_skip_cleanup(self) -> None:
        service = ClassificationService(Settings())
        with (
            patch.object(service.store, "cleanup", return_value=3) as cleanup,
            patch(
                "kanamibot.plugins.image_classifier.service.ImageClassifier",
                side_effect=FileNotFoundError("missing models"),
            ),
            patch("kanamibot.plugins.image_classifier.service.logger") as log,
        ):
            await service.start()
        cleanup.assert_called_once()
        log.exception.assert_called_once()
        self.assertFalse(service.accepting)
        self.assertIsNone(service.worker)

    async def test_disabled_collection_still_cleans_up_on_start(self) -> None:
        service = ClassificationService(Settings(enabled=False))
        with patch.object(service.store, "cleanup", return_value=1) as cleanup:
            await service.start()
        cleanup.assert_called_once()
        self.assertFalse(service.accepting)

    async def test_hook_captures_all_groups_and_cleanup_runs_each_minute(self) -> None:
        from kanamibot.plugins.image_classifier import handlers

        bot = Mock(self_id="100")
        for group in (111, 222):
            event = GroupMessageEvent.model_validate(
                {
                    "time": 1,
                    "self_id": 100,
                    "post_type": "message",
                    "message_type": "group",
                    "sub_type": "normal",
                    "message_id": group,
                    "group_id": group,
                    "user_id": 123,
                    "message": [
                        {"type": "image", "data": {"file": "https://example.test/image"}},
                    ],
                    "original_message": [],
                    "raw_message": "",
                    "font": 0,
                    "sender": {"user_id": 123, "role": "member"},
                }
            )
            with (
                patch.object(handlers.service, "accepting", True),
                patch.object(handlers.service, "submit", new_callable=AsyncMock) as submit,
            ):
                await handlers.collect_group_images(bot, event)
                submit.assert_awaited_once()
                self.assertEqual(submit.call_args.args[0].source["group_id"], group)
        job = handlers.scheduler.get_job("image_classifier_cleanup")
        fire = job.trigger.get_next_fire_time(None, datetime(2026, 11, 4, tzinfo=UTC))
        self.assertEqual(fire.astimezone(UTC), datetime(2026, 11, 4, tzinfo=UTC))
        next_fire = job.trigger.get_next_fire_time(fire, fire)
        self.assertEqual(next_fire - fire, timedelta(minutes=1))
        job = handlers.scheduler.get_job("image_classifier_review")
        fire = job.trigger.get_next_fire_time(None, datetime(2026, 11, 4, tzinfo=UTC))
        self.assertEqual(fire.astimezone(UTC), datetime(2026, 11, 4, 15, tzinfo=UTC))


class ReviewIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_thumbnail_groups_include_all_pages_and_shanghai_day_boundary(self):
        import hashlib

        from kanamibot.plugins.image_classifier.review import daily_preview_pages, preview_page

        now = datetime(2026, 11, 5, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(Path(tmp))
            inputs = []
            for color, when, nsfw in [
                ("purple", datetime(2026, 11, 4, 15, 59, 59, tzinfo=UTC), 0.05),
                ("red", datetime(2026, 11, 4, 16, tzinfo=UTC), 0.05),
                ("green", now, 0.05),
                ("blue", now, 0.05),
                ("yellow", now, 0.4),
                ("red", now, 0.05),  # duplicate still occupies only one tile
            ]:
                image = io.BytesIO()
                Image.new("RGB", (800, 600), color).save(image, format="PNG")
                data = image.getvalue()
                classifier = fake_classifier()
                classifier.nsfw.predict.return_value = {"nsfw": nsfw, "normal": 1 - nsfw}
                store.save(
                    data, classifier.classify(data), source={}, models={}, thresholds={}, now=when
                )
                inputs.append(data)
            settings = Settings(preview_page_size=2)
            pages = list(daily_preview_pages(store, settings, now=now))
            self.assertEqual(len(pages), 3)
            self.assertEqual([len(page[2]) for page in pages], [1, 2, 1])
            for data, text, _ in pages:
                self.assertNotIn(data, inputs)
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertLessEqual(image.width, 445)
                    self.assertLessEqual(image.height, 326)
                self.assertIn("4张", text)
                self.assertNotIn(hashlib.sha256(inputs[0]).hexdigest()[:16], text)
            self.assertIn("本页 A3/B2", pages[0][1])
            self.assertIn("本页 A3/B3", pages[1][1])
            self.assertEqual(preview_page(store, settings, 3, now=now), pages[2][:2])

    async def test_yesterday_delivered_content_excluded_after_restart_per_recipient(self):
        import hashlib

        from kanamibot.plugins.image_classifier.review import daily_preview_pages, preview_page

        now = datetime(2026, 11, 5, 15, tzinfo=UTC)
        digest = hashlib.sha256(png_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(Path(tmp))
            store.save(
                png_bytes(),
                fake_classifier().classify(png_bytes()),
                source={},
                models={},
                thresholds={},
                now=now,
            )
            store.mark_reviewed("123", [digest], now - timedelta(days=1))
            store = ResultStore(Path(tmp))
            self.assertEqual(preview_page(store, Settings(), now=now, user="123")[0], b"")
            self.assertEqual(
                list(daily_preview_pages(store, Settings(), now=now, user="123"))[0][2], []
            )
            self.assertEqual(
                list(daily_preview_pages(store, Settings(), now=now, user="456"))[0][2], [digest]
            )
            store.mark_reviewed("456", [digest], now)
            self.assertEqual(
                list(daily_preview_pages(store, Settings(), now=now, user="456"))[0][2], []
            )
            # Manual review still permits revisiting today's delivered sheet for selection.
            self.assertTrue(preview_page(store, Settings(), now=now, user="456")[0])
            store.cleanup(now=now + timedelta(days=3))
            self.assertIn(digest, store.reviewed_before("123", now))

    async def test_nightly_sends_each_sheet_and_marks_only_successful_pages(self):
        import base64

        from nonebot.adapters.onebot.v11 import Bot

        from kanamibot.plugins.image_classifier import handlers

        bot = Mock(spec=Bot)
        bot.send_private_msg = AsyncMock(side_effect=[RuntimeError("fail"), {}, {}])
        pages = [
            (b"thumbnail-one", "page1", ["a" * 64]),
            (b"thumbnail-two", "page2", ["b" * 64]),
            (b"", "empty", []),
        ]
        with (
            patch.object(handlers.settings, "enabled", True),
            patch.object(handlers.driver.config, "superusers", {"123"}),
            patch.object(handlers, "get_bots", return_value={"100": bot}),
            patch.object(handlers, "daily_preview_pages", return_value=iter(pages)),
            patch.object(handlers.service.store, "mark_reviewed") as mark,
        ):
            await handlers.nightly_review()
        self.assertEqual(bot.send_private_msg.await_count, 3)
        self.assertEqual(mark.call_count, 2)
        self.assertEqual(mark.call_args_list[0].args[1], ["b" * 64])
        payload = bot.send_private_msg.await_args_list[1].kwargs["message"]
        self.assertEqual(len(payload["image"]), 1)
        self.assertEqual(base64.b64decode(payload["image"][0].data["file"][9:]), b"thumbnail-two")
        self.assertFalse(bot.send_private_msg.await_args_list[2].kwargs["message"]["image"])

    async def test_legacy_scores_reclassified_and_ab_preview_filters(self) -> None:
        from dataclasses import replace

        from kanamibot.plugins.image_classifier.review import preview_page

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResultStore(root / "cache")
            original = fake_classifier().classify(png_bytes())
            legacy = replace(
                original,
                dimension="real",
                subtype="non_anime",
                safety="normal",
                anime_real_scores={"anime": 0.8, "real": 0.2},
                nsfw_scores={"nsfw": 0.4, "normal": 0.6},
            )
            path = store.save(png_bytes(), legacy, source={}, models={}, thresholds={})
            old_metadata = json.loads(path.read_text())
            old_metadata["classification"].pop("category_a")
            old_metadata["classification"].pop("category_b")
            old_metadata["schema_version"] = 3
            path.write_text(json.dumps(old_metadata))
            _, updated = store.records()[0]
            self.assertEqual(updated["classification"]["category_a"], "A1")
            self.assertEqual(updated["classification"]["category_b"], "B2")
            self.assertEqual(updated["expires_at"], old_metadata["expires_at"])
            _, text = preview_page(
                store, Settings(preview_categories_a={"A1"}, preview_categories_b={"B2"})
            )
            self.assertIn("1张", text)
            self.assertIn("A1 二次元 / B2 涩图", text)
            _, text = preview_page(store, Settings(preview_categories_b={"B1"}))
            self.assertIn("0张", text)
            self.assertTrue(path.exists())
            self.assertFalse(hasattr(store, "archive"))

    async def test_b2_directory_is_included_in_retention_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            classifier = fake_classifier()
            classifier.nsfw.predict.return_value = {"nsfw": 0.3, "normal": 0.7}
            result = classifier.classify(png_bytes())
            self.assertEqual(result.category_b, "B2")
            store = ResultStore(Path(tmp))
            path = store.save(
                png_bytes(),
                result,
                source={},
                models={},
                thresholds={},
                now=datetime.now(UTC) - timedelta(days=3),
            )
            self.assertEqual(path.parent.parent.name, "suggestive")
            self.assertEqual(store.cleanup(), 1)
            self.assertFalse(path.exists())

    async def test_restart_restores_only_matching_model_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(cache_dir=Path(tmp))
            first = ClassificationService(settings)
            first.classifier = fake_classifier()
            first.classifier.provenance = Mock(return_value={"version": "same"})
            first._classify_and_save(png_bytes(), {})
            second = ClassificationService(settings)
            second.classifier = fake_classifier()
            second.classifier.provenance = Mock(return_value={"version": "same"})
            second.classifier.classify = Mock(wraps=second.classifier.classify)
            second._restore_cache()
            second._classify_and_save(png_bytes(), {})
            second.classifier.classify.assert_not_called()
            paths = list(Path(tmp).rglob("image.png"))
            self.assertEqual(paths[0].stat().st_ino, paths[1].stat().st_ino)
            second._results.clear()
            second.classifier.provenance.return_value = {"version": "changed"}
            second._restore_cache()
            self.assertEqual(len(second._results), 0)
            second.classifier.provenance.return_value = {"version": "same"}
            second.settings.animation_samples = 1
            second._restore_cache()
            self.assertEqual(len(second._results), 0)

    async def test_nightly_delivery_is_private_and_continues_after_failed_recipient(self) -> None:
        from nonebot.adapters.onebot.v11 import Bot

        from kanamibot.plugins.image_classifier import handlers

        bot = Mock(spec=Bot)
        bot.send_private_msg = AsyncMock(side_effect=[RuntimeError("QQ failure"), {}])
        with (
            patch.object(handlers.settings, "enabled", True),
            patch.object(handlers.driver.config, "superusers", {"123", "456"}),
            patch.object(handlers, "get_bots", return_value={"100": bot}),
            patch.object(
                handlers,
                "daily_preview_pages",
                side_effect=lambda *a, **kw: iter([(png_bytes(), "preview", [])]),
            ),
            patch.object(handlers.service.store, "mark_reviewed"),
        ):
            await handlers.nightly_review()
        self.assertEqual(bot.send_private_msg.await_count, 2)
        self.assertEqual(
            {call.kwargs["user_id"] for call in bot.send_private_msg.await_args_list}, {123, 456}
        )

    async def test_local_path_reused_without_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "napcat.png"
            path.write_bytes(png_bytes())
            bot = Mock()
            bot.get_image = AsyncMock(return_value={"file": str(path)})
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: self.fail("Must reuse local file"))
            ) as client:
                items = [
                    item
                    async for item in message_images(
                        bot,
                        Message(MessageSegment.image("opaque-qq-id")),
                        client,
                        10000,
                        [Path(tmp)],
                    )
                ]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0][0], png_bytes())
            self.assertEqual(items[0][1]["local_path"], str(path.resolve()))

    async def test_untrusted_local_path_uses_http(self) -> None:
        from kanamibot.plugins.image_classifier.inputs import read_local_image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private.png"
            path.write_bytes(png_bytes())
            self.assertIsNone(read_local_image([str(path)], [], 10000))
            self.assertIsNone(read_local_image([str(path)], [Path(tmp)], 1))
            self.assertEqual(read_local_image([path.as_uri()], [Path(tmp)], 10000)[0], png_bytes())

    async def test_dedup_inference_hardlinks_and_independent_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ClassificationService(Settings(cache_dir=Path(tmp)))
            classifier = fake_classifier()
            classifier.provenance = Mock(return_value={})
            classifier.classify = Mock(wraps=classifier.classify)
            service.classifier = classifier
            service._classify_and_save(png_bytes(), {})
            service._classify_and_save(png_bytes(), {})
            self.assertEqual(classifier.classify.call_count, 1)
            paths = list(Path(tmp).rglob("image.png"))
            self.assertEqual(len(paths), 2)
            self.assertEqual(paths[0].stat().st_ino, paths[1].stat().st_ino)
            paths[0].unlink()
            self.assertEqual(paths[1].read_bytes(), png_bytes())

    async def test_reference_cleanup_never_removes_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "napcat.png"
            source.write_bytes(png_bytes())
            store = ResultStore(root / "cache")
            path = store.save(
                png_bytes(),
                fake_classifier().classify(png_bytes()),
                source={},
                models={},
                thresholds={},
                reference=source,
            )
            record = json.loads(path.read_text())
            self.assertFalse((path.parent / record["image_file"]).exists())
            self.assertEqual(store.image_bytes(path, record), png_bytes())
            store.cleanup(now=datetime.now(UTC) + timedelta(days=3))
            self.assertTrue(source.exists())
            self.assertFalse(path.exists())

    async def test_preview_filters_deduplicates_and_detects_changed_references(self) -> None:
        from kanamibot.plugins.image_classifier.review import preview_page

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResultStore(root / "cache")
            for _ in range(2):
                store.save(
                    png_bytes(),
                    fake_classifier().classify(png_bytes()),
                    source={},
                    models={},
                    thresholds={},
                )
            data, text = preview_page(store, Settings())
            self.assertTrue(data.startswith(b"\xff\xd8"))
            self.assertIn("1张", text)
            _, text = preview_page(store, Settings(preview_dimensions={"real"}))
            self.assertIn("0张", text)
            source = root / "source.png"
            source.write_bytes(png_bytes())
            path = store.save(
                png_bytes(),
                fake_classifier().classify(png_bytes()),
                source={},
                models={},
                thresholds={},
                reference=source,
            )
            source.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                store.image_bytes(path, json.loads(path.read_text()))

    async def test_private_message_collected_and_self_message_skipped(self) -> None:
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent

        from kanamibot.plugins.image_classifier import handlers

        event = PrivateMessageEvent.model_validate(
            {
                "time": 1,
                "self_id": 100,
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "message_id": 1,
                "user_id": 123,
                "message": [{"type": "image", "data": {"file": "qq-id"}}],
                "raw_message": "",
                "font": 0,
                "sender": {"user_id": 123},
            }
        )
        with (
            patch.object(handlers.service, "accepting", True),
            patch.object(handlers.service, "submit", new_callable=AsyncMock) as submit,
        ):
            await handlers.collect_group_images(Mock(self_id="100"), event)
            self.assertIsNone(submit.call_args.args[0].source["group_id"])
            submit.reset_mock()
            await handlers.collect_group_images(Mock(self_id="123"), event)
            submit.assert_not_awaited()


class ImageRequestTest(unittest.IsolatedAsyncioTestCase):
    async def test_command_groups_and_mixed_commas(self):
        from kanamibot.plugins.image_classifier.commands import group_command, parse_request

        for a in range(1, 4):
            for b in range(1, 4):
                category = (f"A{a}", f"B{b}")
                with self.subTest(category=category):
                    command = "图" + "a" * a + "b" * b
                    self.assertEqual(group_command(category), command)
                    self.assertEqual(
                        parse_request(command + " 3，5,12，25"), (category, [3, 5, 12, 25])
                    )
                    self.assertEqual(parse_request(f"图a{a}b{b} 3"), (category, [3]))
                    self.assertEqual(parse_request(command[1:] + " 3，5"), (category, [3, 5]))
                    self.assertEqual(parse_request(f"a{a}b{b} 3"), (category, [3]))
        self.assertEqual(parse_request("图"), (None, []))
        self.assertEqual(parse_request("图ab"), (("A1", "B1"), []))
        self.assertEqual(parse_request("aabb"), (("A2", "B2"), []))
        self.assertEqual(parse_request("图aabb 3， 5,3"), (("A2", "B2"), [3, 5]))
        for command in (
            "#图ab 3",
            "图片ab 3",
            "图ab 0",
            "图ab -1",
            "图ab 3,,5",
            "图aaaaab 3",
            "图 3",
            "图ab 3/path",
            "图a4b1 3",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_request(command)

    async def test_group_numbering_spans_pages_survives_restart_and_new_images(self):
        import hashlib

        from kanamibot.plugins.image_classifier.review import daily_preview_pages

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResultStore(root)
            classifier = fake_classifier()
            classifier.anime.predict.return_value = {
                "anime_adult": 0.1,
                "anime_contentious": 0.05,
                "anime_non_adult": 0.05,
                "non_anime": 0.8,
            }
            classifier.nsfw.predict.return_value = {"nsfw": 0.4, "normal": 0.6}
            # All source timestamps are already in the past.
            when = datetime.now(UTC) - timedelta(seconds=1)
            for number in range(26):
                buffer = io.BytesIO()
                Image.new("RGB", (8, 6), (number, 10, 20)).save(buffer, format="PNG")
                data = buffer.getvalue()
                store.save(
                    data,
                    classifier.classify(data),
                    source={},
                    models={},
                    thresholds={},
                    now=when + timedelta(microseconds=number),
                )
            pages = list(daily_preview_pages(store, Settings(), user="123", exclude_today=False))
            self.assertEqual(len(pages), 2)
            self.assertIn("[21]", pages[1][1])
            self.assertIn("[25]", pages[1][1])
            self.assertIn("图aabb", pages[1][1])
            ordered = [digest for _, _, digests in pages for digest in digests]
            # An unseen/failed preview must not replace the last displayed snapshot.
            with self.assertRaises(ValueError):
                store.numbered_images("123", ("A2", "B2"), [3])
            store.mark_reviewed("123", ordered, datetime.now(UTC))
            store = ResultStore(root)
            selected = store.numbered_images("123", ("A2", "B2"), [3, 5, 12, 25])
            for number, (path, record) in selected:
                self.assertEqual(record["sha256"], ordered[number - 1])
                self.assertEqual(
                    hashlib.sha256(store.image_bytes(path, record)).hexdigest(), ordered[number - 1]
                )
            store.save(
                png_bytes(), classifier.classify(png_bytes()), source={}, models={}, thresholds={}
            )
            newer = list(daily_preview_pages(store, Settings(), user="123", exclude_today=False))
            self.assertIn("[27]", newer[-1][1])
            self.assertEqual(
                store.numbered_images("123", ("A2", "B2"), [3])[0][1][1]["sha256"], ordered[2]
            )
            store.cleanup(now=datetime.now(UTC) + timedelta(days=3))
            self.assertEqual(store.numbered_images("123", ("A2", "B2"), [3]), [(3, None)])

    async def test_original_request_preserves_requested_order_and_handles_unavailable_images(self):
        import base64
        from types import SimpleNamespace

        from nonebot.exception import FinishedException

        from kanamibot.plugins.image_classifier import handlers

        rows = [(n, (Path("unused"), {"sha256": str(n) * 64})) for n in (3, 5, 12, 25)]
        event = SimpleNamespace(user_id=123, get_plaintext=lambda: "图aabb 3，5,12，25")
        with (
            patch.object(handlers.service.store, "numbered_images", return_value=rows) as lookup,
            patch.object(
                handlers.service.store,
                "image_bytes",
                side_effect=[b"original3", OSError(), b"original12", b"original25"],
            ),
            patch.object(handlers.service.store, "mark_reviewed") as mark,
            patch.object(handlers.images, "send", new_callable=AsyncMock) as send,
            patch.object(
                handlers.images, "finish", new_callable=AsyncMock, side_effect=FinishedException
            ) as finish,
        ):
            with self.assertRaises(FinishedException):
                await handlers.request_images(event)
        lookup.assert_called_once_with("123", ("A2", "B2"), [3, 5, 12, 25])
        self.assertEqual(
            [
                base64.b64decode(call.args[0]["image"][0].data["file"][9:])
                for call in send.await_args_list
            ],
            [b"original3", b"original12", b"original25"],
        )
        mark.assert_not_called()
        self.assertIn("5", finish.call_args.args[0])
        self.assertFalse(hasattr(handlers, "save"))

    async def test_manual_preview_and_original_leave_nightly_statistics_unchanged(self):
        import hashlib
        from types import SimpleNamespace

        from nonebot.exception import FinishedException

        from kanamibot.plugins.image_classifier import handlers
        from kanamibot.plugins.image_classifier.review import daily_preview_pages

        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(Path(tmp))
            classifier = fake_classifier()
            classifier.anime.predict.return_value = {
                "anime_adult": 0.1,
                "anime_contentious": 0.05,
                "anime_non_adult": 0.05,
                "non_anime": 0.8,
            }
            classifier.nsfw.predict.return_value = {"nsfw": 0.4, "normal": 0.6}
            store.save(
                png_bytes(), classifier.classify(png_bytes()), source={}, models={}, thresholds={}
            )
            digest = hashlib.sha256(png_bytes()).hexdigest()
            with (
                patch.object(handlers.service, "store", store),
                patch.object(handlers.images, "send", new_callable=AsyncMock) as send,
                patch.object(
                    handlers.images, "finish", new_callable=AsyncMock, side_effect=FinishedException
                ),
            ):
                for command in ("图aabb", "aabb 1"):
                    with self.assertRaises(FinishedException):
                        await handlers.request_images(
                            SimpleNamespace(
                                user_id=123, get_plaintext=lambda command=command: command
                            )
                        )
                    self.assertEqual(store.reviewed_before("123", datetime.now(UTC)), set())
                    nightly = list(daily_preview_pages(store, Settings(), user="123"))
                    self.assertEqual(nightly[0][2], [digest])
            self.assertEqual(send.await_count, 2)
            self.assertIn("1张", str(send.await_args_list[0].args[0]))
            store.mark_reviewed("123", [digest], datetime.now(UTC))
            self.assertEqual(list(daily_preview_pages(store, Settings(), user="123"))[0][2], [])

    async def test_commands_match_with_optional_prefix_and_allow_only_private_superuser(self):
        import re

        from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

        from kanamibot.plugins.image_classifier import handlers
        from kanamibot.plugins.image_classifier.commands import REQUEST_PATTERN

        for command in ("图aabb", "图aabb 3，5", "aabb 3", "a2b2 3", "图"):
            self.assertIsNotNone(re.search(REQUEST_PATTERN, command))
        for command in ("#图ab 3", "图片ab 3", "hello aabb 3", "abc 3"):
            self.assertIsNone(re.search(REQUEST_PATTERN, command))
        adapter = Mock()
        adapter.get_name.return_value = "OneBot V11"
        adapter.config = handlers.driver.config
        bot = Bot(adapter, "100")
        with patch.object(handlers.driver.config, "superusers", {"123"}):
            for user in (123, 456):
                private = PrivateMessageEvent.model_validate(
                    {
                        "time": 1,
                        "self_id": 100,
                        "post_type": "message",
                        "message_type": "private",
                        "sub_type": "friend",
                        "message_id": 1,
                        "user_id": user,
                        "message": "aabb 3",
                        "raw_message": "aabb 3",
                        "font": 0,
                        "sender": {"user_id": user},
                    }
                )
                self.assertEqual(await handlers.images.permission(bot, private), user == 123)
                group_data = private.model_dump() | {
                    "message_type": "group",
                    "sub_type": "normal",
                    "group_id": 999,
                }
                group = GroupMessageEvent.model_validate(group_data)
                self.assertFalse(await handlers.images.permission(bot, group))


if __name__ == "__main__":
    unittest.main()
