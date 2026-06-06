$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$napcatDir = Join-Path $projectRoot "vendor\NapCat.Shell"
$launcher = if ($env:NAPCAT_WINDOWS_LAUNCHER) { $env:NAPCAT_WINDOWS_LAUNCHER } else { "launcher-user.bat" }
$launcherPath = Join-Path $napcatDir $launcher

$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "napcat.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $launcherPath)) {
  throw "NapCat is not installed or launcher is missing: $launcherPath. Run .\vendor\install_napcat_windows.ps1 first."
}

Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList "/c", "`"$launcherPath`" $args >> `"$logFile`" 2>&1" `
  -WorkingDirectory $napcatDir `
  -WindowStyle Minimized

Write-Host "NapCat backend started in background."
Write-Host "Log: $logFile"
Write-Host "WebUI: http://127.0.0.1:6099/webui/"
