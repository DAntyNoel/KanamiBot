param(
  [switch]$WithNapCat,
  [Alias("NoNapCat")]
  [switch]$NoneBotOnly,
  [Alias("Sync")]
  [switch]$SyncDependencies,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$NapCatArgs
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
$botScript = Join-Path $projectRoot "bot.py"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
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

function Get-DotEnvInt {
  param(
    [string]$Name,
    [int]$Default
  )

  $envFile = Join-Path $projectRoot ".env"
  if (-not (Test-Path -LiteralPath $envFile)) {
    return $Default
  }

  $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*?)\s*$"
  foreach ($line in Get-Content -LiteralPath $envFile -ErrorAction SilentlyContinue) {
    if ($line -match $pattern) {
      $rawValue = $Matches[1].Trim().Trim('"').Trim("'")
      $parsedValue = 0
      if ([int]::TryParse($rawValue, [ref]$parsedValue)) {
        return $parsedValue
      }
    }
  }

  return $Default
}

function Test-LocalPortListening {
  param([int]$Port)

  try {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
      Select-Object -First 1
    return $null -ne $listener
  } catch {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
      $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
      if (-not $connect.AsyncWaitHandle.WaitOne(300)) {
        return $false
      }
      $client.EndConnect($connect)
      return $true
    } catch {
      return $false
    } finally {
      $client.Dispose()
    }
  }
}

function Test-NapCatRuntimeProcessRunning {
  $processes = Get-Process -ErrorAction SilentlyContinue
  if ($processes | Where-Object { $_.ProcessName -match '^NapCat' } | Select-Object -First 1) {
    return $true
  }

  foreach ($qqProcess in $processes | Where-Object { $_.ProcessName -eq "QQ" }) {
    try {
      $napcatModule = $qqProcess.Modules |
        Where-Object { $_.ModuleName -match 'NapCat' -or $_.FileName -match 'NapCat' } |
        Select-Object -First 1
      if ($napcatModule) {
        return $true
      }
    } catch {
      # Some QQ processes deny module enumeration; try their command line next.
    }

    try {
      $snapshot = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $($qqProcess.Id)" `
        -ErrorAction Stop
      if ($snapshot.CommandLine -match 'NapCat') {
        return $true
      }
    } catch {
      # Port and repository PID checks remain available without CIM access.
    }
  }

  return $false
}

function Start-AttachedProcess {
  param(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$WorkingDirectory
  )

  $quotedArguments = $ArgumentList | ForEach-Object {
    if ($null -eq $_ -or $_ -eq "") {
      return '""'
    }
    if ($_ -notmatch '[\s"]') {
      return $_
    }
    return '"' + ($_ -replace '"', '\"') + '"'
  }

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $FilePath
  $startInfo.Arguments = $quotedArguments -join " "
  $startInfo.WorkingDirectory = $WorkingDirectory
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  if (-not $process.Start()) {
    throw "Failed to start attached process: $FilePath"
  }
  return $process
}

$nonebotPort = Get-DotEnvInt -Name "PORT" -Default 12706
$napcatWebUiPort = Get-DotEnvInt -Name "NAPCAT_WEBUI_PORT" -Default 12705
$nonebotRunning = Test-LocalPortListening -Port $nonebotPort
$napcatRunning = (Test-NapCatRuntimeProcessRunning) -or
  (Test-PidFileProcessRunning -Path $napcatPidFile) -or
  (Test-LocalPortListening -Port $napcatWebUiPort)
$napcatProcess = $null

if ($NoneBotOnly) {
  Write-Host "Skipping NapCat startup because NoneBot-only mode was requested."
} elseif ($napcatRunning) {
  Write-Host "NapCat is already running; leaving the existing process untouched."
} else {
  $configuredLauncher = if ($env:NAPCAT_WINDOWS_LAUNCHER) {
    $env:NAPCAT_WINDOWS_LAUNCHER
  } else {
    "launcher-user.bat"
  }
  $napcatLauncher = Join-Path $projectRoot "vendor\NapCat.Shell\$configuredLauncher"
  if (-not (Test-Path -LiteralPath $napcatLauncher)) {
    Write-Host "NapCat is not installed; installing it before startup."
    & $napcatInstallScript
  }

  Write-Host "Starting NapCat in this console."
  $napcatPowerShellArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $napcatStartScript
  ) + $NapCatArgs
  $napcatProcess = Start-AttachedProcess `
    -FilePath "powershell.exe" `
    -ArgumentList $napcatPowerShellArgs `
    -WorkingDirectory $projectRoot
  Write-Host "NapCat launcher attached with PID $($napcatProcess.Id)."
}

if ($nonebotRunning) {
  Write-Host "NoneBot is already listening on port $nonebotPort; leaving it untouched."
} else {
  $env:UV_CACHE_DIR = ".uv-cache"
  if ($SyncDependencies -or -not (Test-Path -LiteralPath $venvPython)) {
    $uvPath = Resolve-UvPath
    Write-Host "Checking and syncing Python dependencies. This may take a moment."
    & $uvPath sync
    if ($LASTEXITCODE -ne 0) {
      throw "Python dependency sync failed with exit code $LASTEXITCODE."
    }
  } else {
    Write-Host "Using the existing virtual environment (dependency sync skipped)."
    Write-Host "Use -SyncDependencies after pyproject.toml or uv.lock changes."
  }

  Write-Host "Starting NoneBot in this console."
  Write-Host "OneBot reverse WebSocket: ws://127.0.0.1:$nonebotPort/onebot/v11/ws"
  $env:PYTHONUNBUFFERED = "1"
  Write-Host "Startup complete. Keep this console open to keep newly started processes running."
  Write-Host "Closing this console stops every process started by this launcher."
  & $venvPython -u $botScript
  $nonebotExitCode = $LASTEXITCODE
}

if ($nonebotRunning -and -not $napcatProcess) {
  Write-Host "All requested services are already running; nothing was changed."
  exit 0
}

if ($napcatProcess -and -not $napcatProcess.HasExited) {
  if ($nonebotRunning) {
    Write-Host "NapCat startup complete. Keep this console open to keep it running."
    Write-Host "Closing this console stops the NapCat process started by this launcher."
  }
  while (-not $napcatProcess.HasExited) {
    Start-Sleep -Milliseconds 500
  }
}

if (-not $nonebotRunning -and $nonebotExitCode -ne 0) {
  exit $nonebotExitCode
}
if ($napcatProcess -and $napcatProcess.ExitCode -ne 0) {
  exit $napcatProcess.ExitCode
}
exit 0
