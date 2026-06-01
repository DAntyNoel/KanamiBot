$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$napcatDir = Join-Path $projectRoot "vendor\NapCatQQ"
$launcher = if ($env:NAPCAT_WINDOWS_LAUNCHER) { $env:NAPCAT_WINDOWS_LAUNCHER } else { "launcher-user.bat" }
$launcherPath = Join-Path $napcatDir $launcher

if (-not (Test-Path $launcherPath)) {
  throw "NapCat is not installed or launcher is missing: $launcherPath. Run .\vendor\install_napcat_windows.ps1 first."
}

Set-Location $napcatDir

Write-Host "Starting NapCat backend in foreground..."
Write-Host "WebUI: http://127.0.0.1:6099/webui/"
Write-Host "Press Ctrl+C to stop."
& $launcherPath @args
exit $LASTEXITCODE
