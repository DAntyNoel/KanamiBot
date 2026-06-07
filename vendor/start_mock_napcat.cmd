@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_mock_napcat.ps1" %*
exit /b %ERRORLEVEL%
