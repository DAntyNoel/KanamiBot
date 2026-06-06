@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%restart_nonebot.ps1" %*
exit /b %ERRORLEVEL%
