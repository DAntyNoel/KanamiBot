$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path $PSScriptRoot
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "kanamibot.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList "/c", "set UV_CACHE_DIR=.uv-cache&& uv run python bot.py >> logs\kanamibot.log 2>&1" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Minimized

Write-Host "KanamiBot NoneBot backend started in background."
Write-Host "Log: $logFile"
