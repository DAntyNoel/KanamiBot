from __future__ import annotations

import asyncio
import io
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from kanamibot.plugins.image_classifier.classifier import Classification
from kanamibot.plugins.image_classifier.config import Settings
from kanamibot.plugins.image_classifier.web import BrowserServer, create_app


def sample_result() -> Classification:
    return Classification(
        dimension="anime",
        subtype="anime_non_adult",
        safety="normal",
        anime_probability=0.9,
        nsfw_probability=0.1,
        original_label="anime_non_adult",
        anime_scores={
            "anime_adult": 0.1,
            "anime_contentious": 0.2,
            "anime_non_adult": 0.6,
            "non_anime": 0.1,
        },
        nsfw_scores={"normal": 0.9, "nsfw": 0.1},
        frames=[],
        total_frames=1,
        image_format="png",
        width=8,
        height=8,
    )


class ClassifierWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(max_download_mb=1, max_pixels=100)
        self.model = Mock()
        self.model.classify.return_value = sample_result()
        self.model.provenance.return_value = {"anime": {"revision": "pinned"}}
        self.model.available_models.return_value = ("anime", "anime_real", "nsfw")

        def unload(key):
            self.model.available_models.return_value = tuple(
                loaded for loaded in self.model.available_models() if loaded != key
            )

        def load(key):
            self.model.available_models.return_value = tuple(
                dict.fromkeys((*self.model.available_models(), key))
            )

        self.model.unload_model.side_effect = unload
        self.model.load_model.side_effect = load
        patcher = patch(
            "kanamibot.plugins.image_classifier.web.ImageClassifier", return_value=self.model
        )
        self.addCleanup(patcher.stop)
        patcher.start()
        self.client = self.enterContext(TestClient(create_app(self.settings)))

    def test_upload_returns_every_class_probability_and_model_provenance(self) -> None:
        response = self.client.post("/api/classify", content=b"image-data")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["classification"]["anime_scores"], sample_result().anime_scores)
        self.assertEqual(payload["classification"]["nsfw_scores"], {"normal": 0.9, "nsfw": 0.1})
        self.assertEqual(payload["models"]["anime"]["revision"], "pinned")
        self.assertEqual(payload["thresholds"], {"anime": 0.5, "nsfw": 0.5})
        self.model.classify.assert_called_once_with(b"image-data")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_request_selection_reaches_inference_without_changing_later_requests(self) -> None:
        response = self.client.post("/api/classify?models=anime_real", content=b"x")
        self.assertEqual(response.status_code, 200)
        self.model.classify.assert_called_with(b"x", ("anime_real",))
        self.client.post("/api/classify?models=anime,nsfw", content=b"y")
        self.model.classify.assert_called_with(b"y", ("anime", "nsfw"))
        self.client.post("/api/classify", content=b"z")
        self.model.classify.assert_called_with(b"z")

    def test_empty_unknown_or_unavailable_selection_is_rejected(self) -> None:
        self.model.available_models.return_value = ("anime", "nsfw")
        for selected in ["", "bad-key", "anime_real"]:
            response = self.client.post(f"/api/classify?models={selected}", content=b"x")
            self.assertEqual(response.status_code, 400)
        self.model.classify.assert_not_called()

    def test_model_is_loaded_once_and_status_reports_upload_limit(self) -> None:
        response = self.client.get("/api/status")
        payload = response.json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["provider"], "CPUExecutionProvider")
        self.assertEqual(payload["max_upload_bytes"], 1024 * 1024)
        for _ in range(2):
            self.assertEqual(self.client.post("/api/classify", content=b"x").status_code, 200)

    def test_unload_all_and_reload_updates_readiness_and_selection(self) -> None:
        for key in ("anime", "anime_real", "nsfw"):
            response = self.client.post(f"/api/models/{key}/unload")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["model_states"][key], {"enabled": True, "loaded": False}
            )
        self.assertFalse(self.client.get("/api/status").json()["ready"])
        self.assertEqual(self.client.post("/api/classify", content=b"x").status_code, 400)
        self.client.post("/api/models/anime/unload")  # Repeating an unload remains harmless.
        response = self.client.post("/api/models/anime_real/load")
        self.assertTrue(response.json()["ready"])
        self.assertEqual(
            response.json()["model_states"]["anime_real"], {"enabled": True, "loaded": True}
        )
        self.assertEqual(
            self.client.post("/api/classify?models=anime", content=b"x").status_code, 400
        )
        self.assertEqual(
            self.client.post("/api/classify?models=anime_real", content=b"x").status_code, 200
        )
        self.model.classify.assert_called_once_with(b"x", ("anime_real",))

    def test_lifecycle_rejects_unknown_or_startup_disabled_model(self) -> None:
        with TestClient(create_app(Settings(anime_enabled=False))) as client:
            for action in ("load", "unload"):
                self.assertEqual(client.post(f"/api/models/unknown/{action}").status_code, 404)
                self.assertEqual(client.post(f"/api/models/anime/{action}").status_code, 403)
            self.assertFalse(client.get("/api/status").json()["model_states"]["anime"]["enabled"])
        self.model.load_model.assert_not_called()
        self.model.unload_model.assert_not_called()

    def test_failed_reload_is_readable_and_retry_can_succeed(self) -> None:
        self.client.post("/api/models/anime/unload")
        load = self.model.load_model.side_effect
        self.model.load_model.side_effect = FileNotFoundError("missing model")
        with patch("kanamibot.plugins.image_classifier.web.logger"):
            response = self.client.post("/api/models/anime/load")
        self.assertEqual(response.status_code, 500)
        self.assertIn("模型加载失败", response.json()["detail"])
        self.assertFalse(self.client.get("/api/status").json()["model_states"]["anime"]["loaded"])
        self.model.load_model.side_effect = load
        self.assertEqual(self.client.post("/api/models/anime/load").status_code, 200)

    def test_static_page_and_scripts_are_served_without_starting_bot(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="file-input"', response.text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)
        self.assertEqual(self.client.get("/static/../../config.py").status_code, 404)

    def test_empty_and_oversized_uploads_do_not_reach_model(self) -> None:
        self.assertEqual(self.client.post("/api/classify", content=b"").status_code, 400)
        self.assertEqual(
            self.client.post("/api/classify", content=b"x" * (1024 * 1024 + 1)).status_code,
            413,
        )
        self.model.classify.assert_not_called()

    def test_streaming_request_without_content_length_obeys_size_limit(self) -> None:
        def body():
            yield b"x" * (600 * 1024)
            yield b"x" * (600 * 1024)

        response = self.client.post("/api/classify", content=body())
        self.assertEqual(response.status_code, 413)
        self.model.classify.assert_not_called()

    def test_decode_and_pixel_failures_are_readable_and_next_image_can_succeed(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="PNG")
        self.model.classify.side_effect = [
            OSError("invalid image"),
            ValueError("Image exceeds pixel limit"),
            sample_result(),
        ]
        corrupt = self.client.post("/api/classify", content=b"not-an-image")
        self.assertEqual(corrupt.status_code, 422)
        self.assertIn("有效的图片", corrupt.json()["detail"])
        too_big = self.client.post("/api/classify", content=buffer.getvalue())
        self.assertEqual(too_big.status_code, 422)
        self.assertIn("100", too_big.json()["detail"])
        self.assertEqual(
            self.client.post("/api/classify", content=buffer.getvalue()).status_code, 200
        )

    def test_missing_models_leave_page_available_with_actionable_status(self) -> None:
        with (
            patch(
                "kanamibot.plugins.image_classifier.web.ImageClassifier",
                side_effect=FileNotFoundError("no models"),
            ),
            patch("kanamibot.plugins.image_classifier.web.logger"),
            TestClient(create_app()) as client,
        ):
            self.assertEqual(client.get("/").status_code, 200)
            status = client.get("/api/status").json()
            self.assertFalse(status["ready"])
            self.assertIn("模型加载失败", status["error"])
            self.assertEqual(client.post("/api/classify", content=b"x").status_code, 503)


class BrowserLaunchTest(unittest.IsolatedAsyncioTestCase):
    async def test_browser_opens_only_after_server_is_listening(self) -> None:
        server = BrowserServer(Mock(), "http://127.0.0.1:12709/")

        async def started(**_kwargs):
            server.started = True

        with (
            patch("uvicorn.Server.startup", new=AsyncMock(side_effect=started)),
            patch("kanamibot.plugins.image_classifier.web.webbrowser.open") as open_browser,
        ):
            await server.startup()
        open_browser.assert_called_once_with("http://127.0.0.1:12709/")

    async def test_no_open_option_does_not_launch_browser(self) -> None:
        server = BrowserServer(Mock(), None)
        with (
            patch("uvicorn.Server.startup", new=AsyncMock()),
            patch("kanamibot.plugins.image_classifier.web.webbrowser.open") as open_browser,
        ):
            server.started = True
            await server.startup()
        open_browser.assert_not_called()


class ModelConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_unload_waits_for_inference_and_result_keeps_its_provenance(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        waiting = asyncio.Event()
        model = Mock()
        loaded = {"anime"}
        model.available_models.side_effect = lambda: tuple(loaded)
        model.provenance.side_effect = lambda: {key: {"revision": "pinned"} for key in loaded}
        model.unload_model.side_effect = loaded.remove

        def classify(*_args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release inference")
            self.assertEqual(loaded, {"anime"})
            return sample_result()

        model.classify.side_effect = classify

        class ObservedLock(asyncio.Lock):
            async def acquire(self):
                if self.locked():
                    waiting.set()
                return await super().acquire()

        with patch("kanamibot.plugins.image_classifier.web.ImageClassifier", return_value=model):
            app = create_app()
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client,
            ):
                app.state.inference_lock = ObservedLock()
                inference = asyncio.create_task(client.post("/api/classify", content=b"x"))
                unload = None
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    unload = asyncio.create_task(client.post("/api/models/anime/unload"))
                    await asyncio.wait_for(waiting.wait(), 5)
                    model.unload_model.assert_not_called()
                finally:
                    release.set()
                    tasks = [inference, *([unload] if unload else [])]
                    responses = await asyncio.gather(*tasks)
                self.assertEqual(responses[0].status_code, 200)
                self.assertEqual(responses[0].json()["models"], {"anime": {"revision": "pinned"}})
                self.assertEqual(responses[1].status_code, 200)
                self.assertFalse(responses[1].json()["ready"])
                self.assertEqual(loaded, set())


if __name__ == "__main__":
    unittest.main()
