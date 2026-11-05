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
        self.assertEqual(result.safety, "normal")

    def test_both_style_models_keep_independent_results_without_averaging(self) -> None:
        classifier = fake_classifier()
        classifier.anime_real = Mock()
        classifier.anime_real.predict.return_value = {"anime": 0.01, "real": 0.99}
        result = classifier.classify(png_bytes())
        self.assertAlmostEqual(result.anime_probability, 0.6)
        self.assertEqual(result.anime_real_scores, {"anime": 0.01, "real": 0.99})
        self.assertEqual(result.dimension_model, "anime")
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

    def test_anime_is_sum_of_three_classes_and_nsfw_is_independent(self) -> None:
        result = summarize(ANIME, NORMAL, Settings())
        self.assertEqual(result["dimension"], "anime")
        self.assertEqual(result["original_label"], "non_anime")
        self.assertAlmostEqual(result["anime_probability"], 0.6)
        self.assertEqual(result["subtype"], "anime_adult")
        self.assertEqual(result["safety"], "normal")
        # "adult" in the first model never overrides the second model's score.
        scores = {"normal": 0.2, "nsfw": 0.8}
        self.assertEqual(summarize(ANIME, scores, Settings())["safety"], "nsfw")

    def test_thresholds_are_configurable(self) -> None:
        result = summarize(
            ANIME,
            {"normal": 0.3, "nsfw": 0.7},
            Settings(
                anime_threshold=0.7,
                nsfw_threshold=0.8,
            ),
        )
        self.assertEqual((result["dimension"], result["subtype"]), ("real", "non_anime"))
        self.assertEqual(result["safety"], "normal")

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
        self.assertEqual(path.parent.parent.parent.name, "anime_adult")
        self.assertEqual(datetime.fromisoformat(record["expires_at"]), self.now + timedelta(days=3))

    def test_cleanup_72h_boundary_and_corrupt_metadata(self) -> None:
        expired = self.save(self.now - timedelta(days=3))
        fresh = self.save(self.now - timedelta(days=3) + timedelta(microseconds=1))
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
        path.parent.symlink_to(outside, target_is_directory=True)
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

    async def test_hook_captures_all_groups_and_cron_uses_shanghai_noon(self) -> None:
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
        self.assertEqual(fire.astimezone(UTC), datetime(2026, 11, 4, 4, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
