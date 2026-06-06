$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$botScript = Join-Path $projectRoot "bot.py"
$stopScript = Join-Path $projectRoot "stopall.ps1"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

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

& $stopScript -NoneBotOnly
$uvPath = Resolve-UvPath

Write-Host "KanamiBot NoneBot backend restarting in foreground."
$env:UV_CACHE_DIR = ".uv-cache"
& $uvPath run python $botScript
exit $LASTEXITCODE
