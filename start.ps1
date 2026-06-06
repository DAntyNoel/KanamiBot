$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "kanamibot.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$napcatInstallScript = Join-Path $projectRoot "vendor\install_napcat_windows.ps1"
$napcatStartScript = Join-Path $projectRoot "vendor\start_kanamibot.ps1"

& $napcatInstallScript
& $napcatStartScript @args

Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList "/c", "set UV_CACHE_DIR=.uv-cache&& uv run python bot.py >> logs\kanamibot.log 2>&1" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Minimized

Write-Host "KanamiBot NoneBot backend started in background."
Write-Host "Log: $logFile"
Write-Host "OneBot reverse WebSocket: ws://127.0.0.1:12706/onebot/v11/ws"
