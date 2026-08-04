from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .backend import decode, decode_input, encode, save_bytes
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent))
    from backend import decode, decode_input, encode, save_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description="小番茄 Gilbert 图片混淆/解混淆")
    parser.add_argument("action", choices=("encode", "decode"))
    parser.add_argument("paths", nargs="*", type=Path, help="一个或多个图片路径")
    parser.add_argument(
        "--base64",
        dest="base64_values",
        action="append",
        default=[],
        help="base64 图片，可重复",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    if not args.paths and not args.base64_values:
        parser.error("至少提供一个图片路径或 --base64")

    transform = encode if args.action == "encode" else decode
    sources: list[tuple[str, bytes]] = [(str(path), path.read_bytes()) for path in args.paths]
    sources.extend(
        (f"base64-{i + 1}", decode_input(value))
        for i, value in enumerate(args.base64_values)
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for number, (label, data) in enumerate(sources, 1):
        output = transform(data)
        stem = Path(label).stem if label.startswith("/") or Path(label).suffix else label
        target = args.output_dir / f"{stem or 'image'}-{args.action}-{number}.jpg"
        save_bytes(output, target)
        print(target)


if __name__ == "__main__":
    main()
