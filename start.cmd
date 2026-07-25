@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
echo [KanamiBot] Starting launcher...
if /I "%~1"=="--nonebot-only" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" -NoneBotOnly
  goto :finished
)

if /I "%~1"=="--no-napcat" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" -NoneBotOnly
  goto :finished
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" %*

:finished
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo [KanamiBot] Launcher failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
