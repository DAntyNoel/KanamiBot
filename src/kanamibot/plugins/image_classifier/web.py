"""Standalone local web UI; does not start NoneBot or connect to QQ."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from .classifier import ImageClassifier
from .compression import prepare_upload
from .config import Settings
from .models import MODEL_KEYS

STATIC_DIR = Path(__file__).with_name("static")
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.classifier = None
        app.state.model_error = None
        app.state.inference_lock = asyncio.Lock()
        try:
            app.state.classifier = await asyncio.to_thread(ImageClassifier, settings)
        except Exception:
            logger.exception("Could not load image models from %s", settings.model_dir)
            app.state.model_error = "模型加载失败，请检查模型目录中的 ONNX 和 manifest 文件后重启。"
        yield
        app.state.classifier = None

    app = FastAPI(title="Kanami Image Lab", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    def status_payload(request: Request) -> dict:
        classifier = request.app.state.classifier
        loaded = classifier.available_models() if classifier else ()
        return {
            "ready": bool(loaded),
            "error": request.app.state.model_error,
            "provider": "CPUExecutionProvider",
            "max_upload_bytes": settings.max_download_mb * 1024 * 1024,
            "max_source_bytes": settings.max_download_mb * 5 * 1024 * 1024,
            "max_pixels": settings.max_pixels,
            "animation_samples": settings.animation_samples,
            "thresholds": {
                "anime": settings.anime_threshold,
                "nsfw": settings.nsfw_threshold,
            },
            "models": classifier.provenance() if classifier else {},
            "model_states": {
                key: {
                    "enabled": getattr(settings, f"{key}_enabled"),
                    "loaded": key in loaded,
                }
                for key in MODEL_KEYS
            },
        }

    @app.get("/api/status")
    async def status(request: Request):
        async with request.app.state.inference_lock:
            return status_payload(request)

    async def change_model(request: Request, model_key: str, *, load: bool):
        if model_key not in MODEL_KEYS:
            raise HTTPException(404, "未知模型。")
        if not getattr(settings, f"{model_key}_enabled"):
            raise HTTPException(403, "此模型已被后台启动配置禁用，请修改启动参数后重启。")
        async with request.app.state.inference_lock:
            classifier = request.app.state.classifier
            if classifier is None:
                raise HTTPException(503, request.app.state.model_error or "模型尚未就绪。")
            try:
                action = classifier.load_model if load else classifier.unload_model
                await asyncio.to_thread(action, model_key)
            except Exception as exc:
                logger.exception("Could not %s model %s", "load" if load else "unload", model_key)
                raise HTTPException(
                    500,
                    "模型加载失败，请检查本地模型文件后重试。"
                    if load
                    else "模型卸载失败，请重试。",
                ) from exc
            return status_payload(request)

    @app.post("/api/models/{model_key}/load")
    async def load_model(request: Request, model_key: str):
        return await change_model(request, model_key, load=True)

    @app.post("/api/models/{model_key}/unload")
    async def unload_model(request: Request, model_key: str):
        return await change_model(request, model_key, load=False)

    @app.post("/api/classify")
    async def classify(request: Request, models: str | None = None):
        classifier = request.app.state.classifier
        if classifier is None:
            raise HTTPException(503, request.app.state.model_error or "模型尚未就绪。")
        selected: tuple[str, ...] | None = None
        if models is not None:
            selected = tuple(key.strip() for key in models.split(",") if key.strip())
            if not selected:
                raise HTTPException(400, "请至少启用一个模型。")
            if set(selected) - set(MODEL_KEYS):
                raise HTTPException(400, "包含未知模型。")
        max_bytes = settings.max_download_mb * 5 * 1024 * 1024
        size_error = f"原文件不能超过 {settings.max_download_mb * 5} MiB。"
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise HTTPException(400, "无效的上传长度。") from exc
            if length < 0:
                raise HTTPException(400, "无效的上传长度。")
            if length > max_bytes:
                raise HTTPException(413, size_error)
        # Serialize reading and inference to bound memory and CPU use across browser tabs.
        async with request.app.state.inference_lock:
            available = classifier.available_models()
            if not available:
                raise HTTPException(400, "所有模型均已卸载，请先加载至少一个模型。")
            if selected is not None and set(selected) - set(available):
                raise HTTPException(400, "所选模型尚未加载或被后台禁用，请先加载后重试。")
            content = bytearray()
            async for chunk in request.stream():
                if len(content) + len(chunk) > max_bytes:
                    raise HTTPException(413, size_error)
                content.extend(chunk)
            if not content:
                raise HTTPException(400, "请上传一张图片。")
            started = time.perf_counter()
            try:
                prepared, preparation = await asyncio.to_thread(
                    prepare_upload, bytes(content), settings
                )
                if selected is None:
                    result = await asyncio.to_thread(classifier.classify, prepared)
                else:
                    result = await asyncio.to_thread(classifier.classify, prepared, selected)
            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise HTTPException(422, "图片像素过大，请缩小后重试。") from exc
            except ValueError as exc:
                if "pixel limit" in str(exc):
                    message = f"图片超过 {settings.max_pixels:,} 像素，请缩小后重试。"
                else:
                    message = "无法处理这张图片，请检查图片内容或更换格式。"
                raise HTTPException(422, message) from exc
            except (UnidentifiedImageError, OSError, EOFError, SyntaxError) as exc:
                raise HTTPException(422, "无法读取图片，请上传有效的图片文件。") from exc
            except Exception as exc:
                logger.exception("Image inference failed")
                raise HTTPException(500, "推理失败，请重试或查看后台日志。") from exc
            provenance = {
                key: info
                for key, info in classifier.provenance().items()
                if selected is None or key in selected
            }
            elapsed_ms = round((time.perf_counter() - started) * 1000)
        # The web preview keeps no uploaded files or result archive on the server.
        return {
            "classification": result.to_dict(),
            "preparation": preparation,
            "elapsed_ms": elapsed_ms,
            "thresholds": {
                "anime": settings.anime_threshold,
                "nsfw": settings.nsfw_threshold,
            },
            "models": provenance,
        }

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


class BrowserServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, browser_url: str | None) -> None:
        super().__init__(config)
        self.browser_url = browser_url

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.browser_url:
            print(f"Image Lab: {self.browser_url}", flush=True)
            try:
                await asyncio.to_thread(webbrowser.open, self.browser_url)
            except Exception:
                logger.warning("Could not open a browser; open %s manually", self.browser_url)


def main() -> None:
    defaults = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12709)
    parser.add_argument("--model-dir", type=Path, default=defaults.model_dir)
    parser.add_argument("--cpu-threads", type=int, default=defaults.cpu_threads)
    parser.add_argument("--anime-threshold", type=float, default=defaults.anime_threshold)
    parser.add_argument("--nsfw-threshold", type=float, default=defaults.nsfw_threshold)
    parser.add_argument("--disable-model", choices=MODEL_KEYS, action="append", default=[])
    parser.add_argument(
        "--no-open", action="store_true", help="Do not open the browser automatically"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    settings = Settings(
        model_dir=args.model_dir,
        cpu_threads=args.cpu_threads,
        anime_threshold=args.anime_threshold,
        nsfw_threshold=args.nsfw_threshold,
        **{f"{key}_enabled": key not in args.disable_model for key in MODEL_KEYS},
    )
    browser_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    url = f"http://{browser_host}:{args.port}/"
    server = BrowserServer(
        uvicorn.Config(create_app(settings), host=args.host, port=args.port),
        browser_url=None if args.no_open else url,
    )
    server.run()


if __name__ == "__main__":
    main()
