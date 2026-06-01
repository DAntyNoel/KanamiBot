@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || exit /b 1

if not exist "logs" mkdir "logs"

start "KanamiBot" /min cmd /c "set UV_CACHE_DIR=.uv-cache&& uv run python bot.py >> logs\kanamibot.log 2>&1"

echo KanamiBot NoneBot backend started in background.
echo Log: %PROJECT_ROOT%logs\kanamibot.log
