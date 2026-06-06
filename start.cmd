@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%" || exit /b 1

if not exist "logs" mkdir "logs"

set "UV_EXE=uv"
where uv >nul 2>nul
if errorlevel 1 (
  if exist "%APPDATA%\Python\Python312\Scripts\uv.exe" (
    set "UV_EXE=%APPDATA%\Python\Python312\Scripts\uv.exe"
  ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\Scripts\uv.exe" (
    set "UV_EXE=%LOCALAPPDATA%\Programs\Python\Python312\Scripts\uv.exe"
  ) else (
    echo uv is not installed or not available in PATH.
    echo Install it with: python -m pip install --user uv
    exit /b 1
  )
)

call "%PROJECT_ROOT%vendor\install_napcat_windows.cmd"
if errorlevel 1 exit /b 1

call "%PROJECT_ROOT%vendor\start_kanamibot.cmd" %*
if errorlevel 1 exit /b 1

start "KanamiBot" /min cmd /c "set UV_CACHE_DIR=.uv-cache&& ""%UV_EXE%"" run python bot.py >> logs\kanamibot.log 2>&1"

echo KanamiBot NoneBot backend started in background.
echo Log: %PROJECT_ROOT%logs\kanamibot.log
echo OneBot reverse WebSocket: ws://127.0.0.1:12706/onebot/v11/ws
