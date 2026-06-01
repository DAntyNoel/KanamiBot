@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || exit /b 1

set "UV_CACHE_DIR=.uv-cache"

echo Starting KanamiBot NoneBot backend in foreground...
echo Press Ctrl+C to stop.
uv run python bot.py
exit /b %errorlevel%
