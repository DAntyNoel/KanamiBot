$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path $PSScriptRoot
Set-Location $projectRoot

$env:UV_CACHE_DIR = ".uv-cache"

Write-Host "Starting KanamiBot NoneBot backend in foreground..."
Write-Host "Press Ctrl+C to stop."
& uv run python bot.py
exit $LASTEXITCODE
