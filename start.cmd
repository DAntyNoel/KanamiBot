@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "NAPCAT_DIR=%PROJECT_ROOT%vendor\NapCat.Shell"
set "NAPCAT_WORKDIR=%PROJECT_ROOT%files\napcat_runtime"
set "NAPCAT_CONFIG_SCRIPT=%PROJECT_ROOT%vendor\configure_napcat_windows.ps1"
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
  if not exist "%NAPCAT_CONFIG_SCRIPT%" (
    echo [KanamiBot] NapCat config script not found: %NAPCAT_CONFIG_SCRIPT%
    pause
    exit /b 1
  )
  echo [KanamiBot] Preparing NapCat WebUI and OneBot configuration...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%NAPCAT_CONFIG_SCRIPT%" -ProjectRoot "%PROJECT_ROOT%" -WorkDir "%NAPCAT_WORKDIR%"
  if errorlevel 1 (
    echo [KanamiBot] NapCat configuration failed.
    pause
    exit /b 1
  )
  echo [KanamiBot] Starting NapCat in a foreground terminal...
  start "KanamiBot NapCat" cmd /k "cd /d %NAPCAT_DIR% && set NAPCAT_WORKDIR=%NAPCAT_WORKDIR%&& call launcher-user.bat"
  echo [KanamiBot] Waiting for NapCat WebUI on port 12705...
  for /L %%I in (1,1,30) do (
    netstat -ano -p tcp | findstr /R /C:":12705 .*LISTENING" >nul
    if not errorlevel 1 goto :napcat_ready
    timeout /t 1 /nobreak >nul
  )
  echo [KanamiBot] NapCat has not opened port 12705 yet. Check its foreground terminal.
)

:napcat_ready
:check_nonebot
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
