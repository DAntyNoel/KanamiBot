"""Run from any working directory; never starts NoneBot or NapCat."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kanamibot.strinova.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
