from __future__ import annotations

import gc
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, Response
from GPT_SoVITS.inference_webui import (
    change_gpt_weights,
    change_sovits_weights,
    get_tts_wav,
)
from pydantic import BaseModel
from tools.i18n.i18n import I18nAuto

GPT_MODEL_PATH = os.getenv("GPT_SOVITS_GPT_MODEL_PATH")
SOVITS_MODEL_PATH = os.getenv("GPT_SOVITS_SOVITS_MODEL_PATH")
TARGET_LANGUAGE = os.getenv("GPT_SOVITS_TARGET_LANGUAGE", "中文")
HOST = os.getenv("GPT_SOVITS_HOST", "127.0.0.1")
PORT = int(os.getenv("GPT_SOVITS_PORT", "9550"))

CURRENT_DIR = Path(__file__).parent
REFS_DIR = CURRENT_DIR / "refs"
DEFAULT_REF_AUDIO = REFS_DIR / "natural" / "在团体公演前稍微热身一下，有助于保持节奏感。.mp3"
DEFAULT_REF_TEXT = "在团体公演前稍微热身一下，有助于保持节奏感。"
DEFAULT_REF_LANG = "中文"
OUTPUT_DIR = CURRENT_DIR / "audio-save"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
i18n = I18nAuto()


def get_random_ref(emotion: str = "natural") -> dict[str, str] | None:
    target_dir = REFS_DIR / emotion
    if not target_dir.exists():
        target_dir = REFS_DIR / "natural"
    if not target_dir.exists():
        return None

    audio_files = [
        path
        for path in target_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".mp3", ".wav"}
    ]
    if not audio_files:
        return None

    selected_file = random.choice(audio_files)
    return {
        "ref_audio_path": str(selected_file.resolve()),
        "ref_text": selected_file.stem.strip(),
        "ref_language": "中文",
    }


class ModelManager:
    def __init__(self) -> None:
        self.last_access_time = time.time()
        self.is_loaded = False
        self.lock = threading.Lock()
        self.monitor_thread = threading.Thread(target=self.timeout_monitor, daemon=True)
        self.monitor_thread.start()

    def load_if_needed(self) -> None:
        with self.lock:
            self.last_access_time = time.time()
            if self.is_loaded:
                return
            change_gpt_weights(gpt_path=GPT_MODEL_PATH)
            change_sovits_weights(sovits_path=SOVITS_MODEL_PATH)
            self.is_loaded = True

    def timeout_monitor(self) -> None:
        while True:
            time.sleep(10)
            if self.is_loaded and time.time() - self.last_access_time > 300:
                with self.lock:
                    if self.is_loaded:
                        self.unload_model()

    def unload_model(self) -> None:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        self.is_loaded = False


manager = ModelManager()


@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "msg": "TTS Server is running"}


class TTSRequest(BaseModel):
    text: str
    emotion: str = "natural"


@app.post("/tts")
def generate_tts(req: TTSRequest) -> Response:
    manager.load_if_needed()
    preset = get_random_ref(req.emotion)
    if preset:
        ref_audio = preset["ref_audio_path"]
        ref_text = preset["ref_text"]
        ref_lang = preset["ref_language"]
    else:
        ref_audio = str(DEFAULT_REF_AUDIO)
        ref_text = DEFAULT_REF_TEXT
        ref_lang = DEFAULT_REF_LANG

    synthesis_result = get_tts_wav(
        ref_wav_path=ref_audio,
        prompt_text=ref_text,
        prompt_language=i18n(ref_lang),
        text=req.text,
        text_language=i18n(TARGET_LANGUAGE),
        top_p=1,
        temperature=1,
    )
    result_list = list(synthesis_result)
    if not result_list:
        return Response(content=b"Error", status_code=500)

    sampling_rate, audio_data = result_list[-1]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_text = re.sub(r'[\\/*?:"<>|]', "", req.text).strip()[:20]
    filename = f"{timestamp}_{safe_text or 'tts'}.wav"
    file_path = OUTPUT_DIR / filename

    sf.write(file_path, audio_data, sampling_rate)
    return Response(content=file_path.read_bytes(), media_type="audio/wav")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
