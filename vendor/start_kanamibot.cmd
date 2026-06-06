@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "NAPCAT_DIR=%PROJECT_ROOT%\vendor\NapCat.Shell"
set "NAPCAT_WORKDIR=%PROJECT_ROOT%\files\napcat_runtime"
if "%NAPCAT_WINDOWS_LAUNCHER%"=="" set "NAPCAT_WINDOWS_LAUNCHER=launcher-user.bat"
set "NAPCAT_LAUNCHER=%NAPCAT_DIR%\%NAPCAT_WINDOWS_LAUNCHER%"

if not exist "%PROJECT_ROOT%\logs" mkdir "%PROJECT_ROOT%\logs"

if not exist "%NAPCAT_LAUNCHER%" (
  echo NapCat is not installed or launcher is missing: %NAPCAT_LAUNCHER%
  echo Run vendor\install_napcat_windows.cmd first.
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_napcat_windows.ps1" -ProjectRoot "%PROJECT_ROOT%" -WorkDir "%NAPCAT_WORKDIR%"
if errorlevel 1 exit /b 1

start "NapCat" /D "%NAPCAT_DIR%" /min cmd /c "set NAPCAT_WORKDIR=%NAPCAT_WORKDIR%&& ""%NAPCAT_LAUNCHER%"" %* >> ""%PROJECT_ROOT%\logs\napcat.log"" 2>&1"

echo NapCat backend started in background.
echo Log: %PROJECT_ROOT%\logs\napcat.log
echo WebUI: http://127.0.0.1:12705/webui/
