#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAPCAT_DIR="$PROJECT_ROOT/vendor/NapCatQQ"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/napcat.log"
PID_FILE="$LOG_DIR/napcat.pid"

mkdir -p "$LOG_DIR"

if [[ ! -f "$NAPCAT_DIR/napcat.mjs" ]]; then
  echo "NapCat is not installed. Run ./vendor/install_napcat_macos.sh first." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "node is required to start NapCat on macOS." >&2
  exit 1
fi

cd "$NAPCAT_DIR"
nohup node napcat.mjs "$@" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

echo "NapCat backend started in background."
echo "PID: $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
echo "WebUI: http://127.0.0.1:6099/webui/"
