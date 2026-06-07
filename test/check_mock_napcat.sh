#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(dirname "$0")"
PROJECT_ROOT="$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)"
MOCK_PROJECT="$PROJECT_ROOT/vendor/mock_napcat"
cd "$PROJECT_ROOT"

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
exec uv run --project "$MOCK_PROJECT" mock-napcat smoke "$@"
