#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"

cd "$PROJECT_ROOT"
mkdir -p "$LOG_DIR"

"$PROJECT_ROOT/stopall.sh" --nonebot-only

echo "KanamiBot NoneBot backend restarting in foreground."
exec env UV_CACHE_DIR=.uv-cache uv run python "$PROJECT_ROOT/bot.py"
