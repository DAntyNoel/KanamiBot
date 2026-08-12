@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "NAPCAT_DIR=%PROJECT_ROOT%vendor\NapCat.Shell"
set "NONEBOT_PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"

echo [KanamiBot] Checking services...

netstat -ano -p tcp | findstr /R /C:":12705 .*LISTENING" >nul
if not errorlevel 1 (
  echo [KanamiBot] NapCat is already listening on port 12705.
) else (
  if not exist "%NAPCAT_DIR%\launcher-user.bat" (
    echo [KanamiBot] NapCat launcher not found: %NAPCAT_DIR%\launcher-user.bat
    pause
    exit /b 1
  )
  echo [KanamiBot] Starting NapCat in a foreground terminal...
  start "KanamiBot NapCat" cmd /k "cd /d %NAPCAT_DIR% && call launcher-user.bat"
)

netstat -ano -p tcp | findstr /R /C:":12706 .*LISTENING" >nul
if not errorlevel 1 (
  echo [KanamiBot] NoneBot is already listening on port 12706.
) else (
  if not exist "%NONEBOT_PYTHON%" (
    echo [KanamiBot] Python virtual environment not found: %NONEBOT_PYTHON%
    echo [KanamiBot] Run uv sync before starting the bot.
    pause
    exit /b 1
  )
  echo [KanamiBot] Starting NoneBot in a foreground terminal...
  start "KanamiBot NoneBot" cmd /k "cd /d %PROJECT_ROOT% && set PYTHONUNBUFFERED=1 && %NONEBOT_PYTHON% -u bot.py"
)

echo [KanamiBot] Startup commands dispatched.
exit /b 0
