$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "kanamibot.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$napcatInstallScript = Join-Path $projectRoot "vendor\install_napcat_windows.ps1"
$napcatStartScript = Join-Path $projectRoot "vendor\start_kanamibot.ps1"

function Resolve-UvPath {
  $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
  if ($uvCommand) {
    return $uvCommand.Source
  }

  $candidates = @(
    (Join-Path $env:APPDATA "Python\Python312\Scripts\uv.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts\uv.exe")
  )

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
  }

  throw "uv is not installed or not available in PATH. Install it with: python -m pip install --user uv"
}

$uvPath = Resolve-UvPath

& $napcatInstallScript
& $napcatStartScript @args

Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList "/c", "set UV_CACHE_DIR=.uv-cache&& `"$uvPath`" run python bot.py >> logs\kanamibot.log 2>&1" `
  -WorkingDirectory $projectRoot `
  -WindowStyle Minimized

Write-Host "KanamiBot NoneBot backend started in background."
Write-Host "Log: $logFile"
Write-Host "OneBot reverse WebSocket: ws://127.0.0.1:12706/onebot/v11/ws"
