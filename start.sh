#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/kanamibot.log"
PID_FILE="$LOG_DIR/kanamibot.pid"

cd "$PROJECT_ROOT"
mkdir -p "$LOG_DIR"

nohup env UV_CACHE_DIR=.uv-cache uv run python bot.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

echo "KanamiBot NoneBot backend started in background."
echo "PID: $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
