#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_ROOT"

echo "Starting KanamiBot NoneBot backend in foreground..."
echo "Press Ctrl+C to stop."
exec env UV_CACHE_DIR=.uv-cache uv run python bot.py
