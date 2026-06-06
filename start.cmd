@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || exit /b 1

if not exist "logs" mkdir "logs"

call "%PROJECT_ROOT%vendor\install_napcat_windows.cmd"
if errorlevel 1 exit /b 1

call "%PROJECT_ROOT%vendor\start_kanamibot.cmd" %*
if errorlevel 1 exit /b 1

start "KanamiBot" /min cmd /c "set UV_CACHE_DIR=.uv-cache&& uv run python bot.py >> logs\kanamibot.log 2>&1"

echo KanamiBot NoneBot backend started in background.
echo Log: %PROJECT_ROOT%logs\kanamibot.log
echo OneBot reverse WebSocket: ws://127.0.0.1:12706/onebot/v11/ws
