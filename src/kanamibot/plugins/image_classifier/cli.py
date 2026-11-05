"""Classify local examples without starting or connecting a bot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .classifier import RULES, ImageClassifier
from .config import Settings
from .models import MODEL_KEYS
from .storage import ResultStore


def main() -> None:
    defaults = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path)
    parser.add_argument("--model-dir", type=Path, default=defaults.model_dir)
    parser.add_argument("--cache-dir", type=Path, default=defaults.cache_dir)
    parser.add_argument("--disable-model", choices=MODEL_KEYS, action="append", default=[])
    parser.add_argument("--cleanup", action="store_true", help="Remove records at least 48h old")
    args = parser.parse_args()
    settings = Settings(
        model_dir=args.model_dir,
        cache_dir=args.cache_dir,
        **{f"{key}_enabled": key not in args.disable_model for key in MODEL_KEYS},
    )
    store = ResultStore(settings.cache_dir)
    if args.cleanup:
        print(json.dumps({"deleted": store.cleanup()}))
    if not args.images:
        if not args.cleanup:
            parser.error("Provide image paths or --cleanup")
        return
    classifier = ImageClassifier(settings)
    for image in args.images:
        data = image.read_bytes()
        result = classifier.classify(data)
        record = store.save(
            data,
            result,
            source={"local_file": str(image.resolve())},
            models=classifier.provenance(),
            thresholds=RULES.copy(),
        )
        print(
            json.dumps(
                {
                    "input": str(image),
                    "result_file": str(record),
                    **result.to_dict(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
