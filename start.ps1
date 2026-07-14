param(
  [switch]$WithNapCat,
  [Alias("NoNapCat")]
  [switch]$NoneBotOnly,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$NapCatArgs
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$botScript = Join-Path $projectRoot "bot.py"
$napcatPidFile = Join-Path $logDir "napcat.pid"
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

function Test-PidFileProcessRunning {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return $false
  }

  $rawPid = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | Select-Object -First 1
  $pidValue = 0
  if (-not [int]::TryParse($rawPid, [ref]$pidValue)) {
    return $false
  }

  $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
  if (-not $process) {
    return $false
  }

  try {
    $snapshot = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction Stop
    $commandLine = $snapshot.CommandLine
    if ($commandLine) {
      return ($commandLine.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) -or
        ($commandLine.IndexOf("KanamiBot NapCat", [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
    }
  } catch {
    return $true
  }

  return $true
}

$uvPath = Resolve-UvPath

if ($NoneBotOnly -or -not $WithNapCat) {
  Write-Host "Skipping NapCat startup because NoneBot-only mode was requested."
  if (-not $NoneBotOnly) {
    Write-Host "NapCat startup is opt-in. Use -WithNapCat only when this repo should own NapCat."
  }
} elseif (Test-PidFileProcessRunning -Path $napcatPidFile) {
  Write-Host "NapCat backend already appears to be running from logs\napcat.pid."
  Write-Host "Skipping NapCat startup; starting NoneBot only."
} else {
  & $napcatInstallScript
  & $napcatStartScript -NewTerminal @NapCatArgs
}

Write-Host "KanamiBot NoneBot backend starting in foreground."
Write-Host "OneBot reverse WebSocket: ws://127.0.0.1:12706/onebot/v11/ws"
$env:UV_CACHE_DIR = ".uv-cache"
& $uvPath run python $botScript
exit $LASTEXITCODE
