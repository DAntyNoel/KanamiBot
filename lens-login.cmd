@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [KanamiBot] Python environment missing. Run uv sync first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -u "%~dp0scripts\lens_login.py" %*
set "LENS_LOGIN_EXIT=%ERRORLEVEL%"
if not "%LENS_LOGIN_EXIT%"=="0" (
  echo [KanamiBot] Verification did not finish. Check the proxy and try again.
)
pause
exit /b %LENS_LOGIN_EXIT%
