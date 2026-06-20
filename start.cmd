@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if /I "%~1"=="--nonebot-only" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" -NoneBotOnly
  exit /b %ERRORLEVEL%
)

if /I "%~1"=="--no-napcat" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" -NoneBotOnly
  exit /b %ERRORLEVEL%
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start.ps1" %*
exit /b %ERRORLEVEL%
