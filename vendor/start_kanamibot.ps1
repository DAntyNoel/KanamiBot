$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$napcatDir = Join-Path $projectRoot "vendor\NapCat.Shell"
$napcatWorkDir = Join-Path $projectRoot "files\napcat_runtime"
$launcher = if ($env:NAPCAT_WINDOWS_LAUNCHER) { $env:NAPCAT_WINDOWS_LAUNCHER } else { "launcher-user.bat" }
$launcherPath = Join-Path $napcatDir $launcher
$configScript = Join-Path $PSScriptRoot "configure_napcat_windows.ps1"

$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "napcat.log"
$pidFile = Join-Path $logDir "napcat.pid"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $launcherPath)) {
  throw "NapCat is not installed or launcher is missing: $launcherPath. Run .\vendor\install_napcat_windows.ps1 first."
}

$launcherArgs = @($args) -join " "
& $configScript -ProjectRoot $projectRoot -WorkDir $napcatWorkDir
$command = "set `"NAPCAT_WORKDIR=$napcatWorkDir`"&& `"$launcherPath`" $launcherArgs >> `"$logFile`" 2>&1"

$napcatProcess = Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList "/c", $command `
  -WorkingDirectory $napcatDir `
  -WindowStyle Minimized `
  -PassThru

Set-Content -Encoding ASCII -Path $pidFile -Value $napcatProcess.Id

Write-Host "NapCat backend started in background."
Write-Host "PID: $($napcatProcess.Id)"
Write-Host "Log: $logFile"
Write-Host "WebUI: http://127.0.0.1:12705/webui/"
