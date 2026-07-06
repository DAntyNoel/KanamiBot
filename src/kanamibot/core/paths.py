from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
FILES_DIR = PROJECT_ROOT / "files"
FONTS_DIR = FILES_DIR / "fonts"
DEFAULT_FONT_PATH = FONTS_DIR / "MiSans-Regular.ttf"
