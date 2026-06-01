@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "NAPCAT_DIR=%PROJECT_ROOT%\vendor\NapCatQQ"
if "%NAPCAT_WINDOWS_LAUNCHER%"=="" set "NAPCAT_WINDOWS_LAUNCHER=launcher-user.bat"
set "NAPCAT_LAUNCHER=%NAPCAT_DIR%\%NAPCAT_WINDOWS_LAUNCHER%"

if not exist "%PROJECT_ROOT%\logs" mkdir "%PROJECT_ROOT%\logs"

if not exist "%NAPCAT_LAUNCHER%" (
  echo NapCat is not installed or launcher is missing: %NAPCAT_LAUNCHER%
  echo Run vendor\install_napcat_windows.cmd first.
  exit /b 1
)

cd /d "%NAPCAT_DIR%" || exit /b 1

echo Starting NapCat backend in foreground...
echo WebUI: http://127.0.0.1:6099/webui/
echo Press Ctrl+C to stop.
call "%NAPCAT_LAUNCHER%" %*
exit /b %errorlevel%
