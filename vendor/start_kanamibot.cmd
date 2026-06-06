@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "NAPCAT_DIR=%PROJECT_ROOT%\vendor\NapCat.Shell"
if "%NAPCAT_WINDOWS_LAUNCHER%"=="" set "NAPCAT_WINDOWS_LAUNCHER=launcher-user.bat"
set "NAPCAT_LAUNCHER=%NAPCAT_DIR%\%NAPCAT_WINDOWS_LAUNCHER%"

if not exist "%PROJECT_ROOT%\logs" mkdir "%PROJECT_ROOT%\logs"

if not exist "%NAPCAT_LAUNCHER%" (
  echo NapCat is not installed or launcher is missing: %NAPCAT_LAUNCHER%
  echo Run vendor\install_napcat_windows.cmd first.
  exit /b 1
)

start "NapCat" /D "%NAPCAT_DIR%" /min cmd /c ""%NAPCAT_LAUNCHER%" %* >> "%PROJECT_ROOT%\logs\napcat.log" 2>&1"

echo NapCat backend started in background.
echo Log: %PROJECT_ROOT%\logs\napcat.log
echo WebUI: http://127.0.0.1:6099/webui/
