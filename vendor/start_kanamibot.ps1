param(
  [switch]$NewTerminal,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$LauncherArgs
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$napcatDir = Join-Path $projectRoot "vendor\NapCat.Shell"
$napcatWorkDir = Join-Path $projectRoot "files\napcat_runtime"
$launcher = if ($env:NAPCAT_WINDOWS_LAUNCHER) { $env:NAPCAT_WINDOWS_LAUNCHER } else { "launcher-user.bat" }
$launcherPath = Join-Path $napcatDir $launcher
$configScript = Join-Path $PSScriptRoot "configure_napcat_windows.ps1"

$logDir = Join-Path $projectRoot "logs"
$pidFile = Join-Path $logDir "napcat.pid"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $launcherPath)) {
  throw "NapCat is not installed or launcher is missing: $launcherPath. Run .\vendor\install_napcat_windows.ps1 first."
}

& $configScript -ProjectRoot $projectRoot -WorkDir $napcatWorkDir

function ConvertTo-CmdArgument {
  param([string]$Value)

  if ($null -eq $Value) {
    return '""'
  }

  return '"' + ($Value -replace '"', '\"') + '"'
}

$launcherArgText = ($LauncherArgs | ForEach-Object { ConvertTo-CmdArgument $_ }) -join " "

if ($NewTerminal) {
  $commandParts = @(
    "title KanamiBot NapCat",
    "chcp 65001 > nul",
    "set `"NAPCAT_WORKDIR=$napcatWorkDir`"",
    "`"$launcherPath`" $launcherArgText"
  )
  $command = $commandParts -join " && "

  $napcatProcess = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList "/c", $command `
    -WorkingDirectory $napcatDir `
    -WindowStyle Normal `
    -PassThru

  Set-Content -Encoding ASCII -Path $pidFile -Value $napcatProcess.Id

  Write-Host "NapCat backend started in an attached terminal."
  Write-Host "Close the NapCat terminal to stop the NapCat backend."
  Write-Host "PID: $($napcatProcess.Id)"
  Write-Host "WebUI: http://127.0.0.1:12705/webui/"
  exit 0
}

Set-Content -Encoding ASCII -Path $pidFile -Value $PID

Write-Host "NapCat backend starting in this terminal."
Write-Host "Close this terminal to stop the NapCat backend."
Write-Host "WebUI: http://127.0.0.1:12705/webui/"

Push-Location $napcatDir
try {
  $env:NAPCAT_WORKDIR = $napcatWorkDir
  & $launcherPath @LauncherArgs
  exit $LASTEXITCODE
} finally {
  Pop-Location
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
