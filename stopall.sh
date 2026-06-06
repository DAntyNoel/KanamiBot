#!/usr/bin/env bash
set -euo pipefail

NONEBOT_ONLY=0
if [[ "${1:-}" == "--nonebot-only" ]]; then
  NONEBOT_ONLY=1
  shift
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
NONEBOT_PID_FILES=("$LOG_DIR/nonebot.pid" "$LOG_DIR/kanamibot.pid")
NAPCAT_PID_FILES=("$LOG_DIR/napcat.pid")

cmdline_contains() {
  local pid="$1"
  local needle="$2"

  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq "$needle"
}

collect_pid_files() {
  local validator="$1"
  shift

  local pid_file pid
  for pid_file in "$@"; do
    [[ -f "$pid_file" ]] || continue
    pid="$(head -n 1 "$pid_file" || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    "$validator" "$pid" && printf '%s\n' "$pid"
  done
}

collect_children() {
  local pid="$1"
  local child

  printf '%s\n' "$pid"
  while read -r child; do
    [[ -n "$child" ]] || continue
    collect_children "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
}

stop_pid_tree() {
  local label="$1"
  shift

  local pids=("$@")
  local tree=()
  local pid

  for pid in "${pids[@]}"; do
    [[ -n "$pid" ]] || continue
    while read -r child; do
      [[ -n "$child" ]] && tree+=("$child")
    done < <(collect_children "$pid")
  done

  if [[ "${#tree[@]}" -eq 0 ]]; then
    echo "No KanamiBot $label process found."
    return
  fi

  local index
  for ((index = ${#tree[@]} - 1; index >= 0; index--)); do
    kill "${tree[$index]}" 2>/dev/null || true
  done

  sleep 1

  for ((index = ${#tree[@]} - 1; index >= 0; index--)); do
    kill -0 "${tree[$index]}" 2>/dev/null && kill -KILL "${tree[$index]}" 2>/dev/null || true
  done

  echo "Stopped KanamiBot $label process tree: ${#tree[@]} process(es)."
}

validate_nonebot_pid() {
  local pid="$1"

  cmdline_contains "$pid" "$PROJECT_ROOT/bot.py" ||
    { cmdline_contains "$pid" "UV_CACHE_DIR=.uv-cache" &&
      cmdline_contains "$pid" "kanamibot.log" &&
      cmdline_contains "$pid" "bot.py"; }
}

validate_napcat_pid() {
  local pid="$1"

  cmdline_contains "$pid" "$PROJECT_ROOT/files/napcat_runtime" ||
    cmdline_contains "$pid" "$PROJECT_ROOT/logs/napcat.log"
}

mapfile -t nonebot_pids < <(
  collect_pid_files validate_nonebot_pid "${NONEBOT_PID_FILES[@]}"
  pgrep -f "$PROJECT_ROOT/bot.py" 2>/dev/null || true
)
mapfile -t nonebot_pids < <(printf '%s\n' "${nonebot_pids[@]}" | awk 'NF && !seen[$0]++')
stop_pid_tree "NoneBot backend" "${nonebot_pids[@]}"
rm -f "${NONEBOT_PID_FILES[@]}"

if [[ "$NONEBOT_ONLY" -eq 0 ]]; then
  mapfile -t napcat_pids < <(
    collect_pid_files validate_napcat_pid "${NAPCAT_PID_FILES[@]}"
    pgrep -f "$PROJECT_ROOT/files/napcat_runtime" 2>/dev/null || true
    pgrep -f "$PROJECT_ROOT/logs/napcat.log" 2>/dev/null || true
  )
  mapfile -t napcat_pids < <(printf '%s\n' "${napcat_pids[@]}" | awk 'NF && !seen[$0]++')
  stop_pid_tree "NapCat backend" "${napcat_pids[@]}"
  rm -f "${NAPCAT_PID_FILES[@]}"
fi
