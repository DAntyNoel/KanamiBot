param(
  [switch]$NoneBotOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$logDir = Join-Path $projectRoot "logs"
$botScript = Join-Path $projectRoot "bot.py"
$nonebotLogFile = Join-Path $logDir "kanamibot.log"
$napcatLogFile = Join-Path $logDir "napcat.log"
$napcatWorkDir = Join-Path $projectRoot "files\napcat_runtime"
$nonebotPidFiles = @(
  (Join-Path $logDir "nonebot.pid"),
  (Join-Path $logDir "kanamibot.pid")
)
$napcatPidFiles = @(
  (Join-Path $logDir "napcat.pid")
)
$script:ProcessSnapshotUnavailable = $false

function Test-CommandLineContains {
  param(
    [AllowNull()][string]$CommandLine,
    [string]$Needle
  )

  if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrEmpty($Needle)) {
    return $false
  }

  return $CommandLine.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-ProcessSnapshot {
  try {
    return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })
  } catch {
    $script:ProcessSnapshotUnavailable = $true
    Write-Warning "Failed to inspect process command lines. Falling back to pid files only: $($_.Exception.Message)"
    return @()
  }
}

function Remove-PidFiles {
  param([string[]]$PidFiles)

  foreach ($pidFile in $PidFiles) {
    if (Test-Path -LiteralPath $pidFile) {
      Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
  }
}

function Get-PidFileProcessIds {
  param(
    [string[]]$PidFiles,
    [object[]]$Processes,
    [scriptblock]$Validator
  )

  $ids = New-Object System.Collections.Generic.List[int]
  foreach ($pidFile in $PidFiles) {
    if (-not (Test-Path -LiteralPath $pidFile)) {
      continue
    }

    $rawPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($rawPid, [ref]$pidValue)) {
      continue
    }

    $process = $Processes | Where-Object { $_.ProcessId -eq $pidValue } | Select-Object -First 1
    if ($process -and (& $Validator $process.CommandLine)) {
      [void]$ids.Add($pidValue)
    }
  }

  return $ids.ToArray()
}

function Get-RawPidFileProcessIds {
  param([string[]]$PidFiles)

  $ids = New-Object System.Collections.Generic.List[int]
  foreach ($pidFile in $PidFiles) {
    if (-not (Test-Path -LiteralPath $pidFile)) {
      continue
    }

    $rawPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($rawPid, [ref]$pidValue)) {
      continue
    }

    if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
      [void]$ids.Add($pidValue)
    }
  }

  return $ids.ToArray()
}

function Get-MatchingProcessIds {
  param(
    [object[]]$Processes,
    [scriptblock]$Predicate
  )

  $ids = New-Object System.Collections.Generic.List[int]
  foreach ($process in $Processes) {
    if (& $Predicate $process) {
      [void]$ids.Add([int]$process.ProcessId)
    }
  }

  return $ids.ToArray()
}

function Get-ProcessTree {
  param(
    [object[]]$Processes,
    [int[]]$RootProcessIds
  )

  $processById = @{}
  $childrenByParent = @{}
  foreach ($process in $Processes) {
    $processId = [int]$process.ProcessId
    $parentId = [int]$process.ParentProcessId
    $processById[$processId] = $process

    if (-not $childrenByParent.ContainsKey($parentId)) {
      $childrenByParent[$parentId] = New-Object System.Collections.Generic.List[object]
    }
    [void]$childrenByParent[$parentId].Add($process)
  }

  $seen = @{}
  $ordered = New-Object System.Collections.Generic.List[object]
  $queue = [System.Collections.Generic.Queue[int]]::new()
  foreach ($rootProcessId in ($RootProcessIds | Sort-Object -Unique)) {
    $queue.Enqueue([int]$rootProcessId)
  }

  while ($queue.Count -gt 0) {
    $currentProcessId = $queue.Dequeue()
    if ($seen.ContainsKey($currentProcessId)) {
      continue
    }

    $seen[$currentProcessId] = $true
    if (-not $processById.ContainsKey($currentProcessId)) {
      continue
    }

    $process = $processById[$currentProcessId]
    [void]$ordered.Add($process)

    if ($childrenByParent.ContainsKey($currentProcessId)) {
      foreach ($child in $childrenByParent[$currentProcessId]) {
        $queue.Enqueue([int]$child.ProcessId)
      }
    }
  }

  return $ordered.ToArray()
}

function Stop-MatchedProcesses {
  param(
    [string]$Label,
    [string[]]$PidFiles,
    [scriptblock]$PidValidator,
    [scriptblock]$Predicate
  )

  $processes = @(Get-ProcessSnapshot)
  $rootProcessIds = @()
  if ($script:ProcessSnapshotUnavailable) {
    $rootProcessIds += Get-RawPidFileProcessIds -PidFiles $PidFiles
  } else {
    $rootProcessIds += Get-PidFileProcessIds -PidFiles $PidFiles -Processes $processes -Validator $PidValidator
    $rootProcessIds += Get-MatchingProcessIds -Processes $processes -Predicate $Predicate
  }
  $rootProcessIds = @($rootProcessIds | Sort-Object -Unique)

  if ($rootProcessIds.Count -eq 0) {
    Write-Host "No KanamiBot $Label process found."
    Remove-PidFiles -PidFiles $PidFiles
    return
  }

  if ($script:ProcessSnapshotUnavailable) {
    $stoppedCount = 0
    foreach ($rootProcessId in $rootProcessIds) {
      & taskkill.exe /PID $rootProcessId /T /F | Out-Null
      if ($LASTEXITCODE -eq 0) {
        $stoppedCount += 1
      } else {
        Write-Warning "Failed to stop PID ${rootProcessId} with taskkill."
      }
    }

    Remove-PidFiles -PidFiles $PidFiles
    Write-Host "Stopped KanamiBot $Label process tree from pid file: $stoppedCount root process(es)."
    return
  }

  $processTree = @(Get-ProcessTree -Processes $processes -RootProcessIds $rootProcessIds)
  if ($processTree.Count -eq 0) {
    Write-Host "No KanamiBot $Label process found."
    Remove-PidFiles -PidFiles $PidFiles
    return
  }

  [array]::Reverse($processTree)
  $stoppedCount = 0
  foreach ($process in $processTree) {
    if ([int]$process.ProcessId -eq $PID) {
      continue
    }

    try {
      Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
      $stoppedCount += 1
    } catch {
      Write-Warning "Failed to stop PID $($process.ProcessId): $($_.Exception.Message)"
    }
  }

  Remove-PidFiles -PidFiles $PidFiles
  Write-Host "Stopped KanamiBot $Label process tree: $stoppedCount process(es)."
}

$nonebotValidator = {
  param([AllowNull()][string]$CommandLine)

  return (Test-CommandLineContains $CommandLine $botScript) -or
    ((Test-CommandLineContains $CommandLine $nonebotLogFile) -and (Test-CommandLineContains $CommandLine "bot.py")) -or
    ((Test-CommandLineContains $CommandLine "UV_CACHE_DIR=.uv-cache") -and
      (Test-CommandLineContains $CommandLine "kanamibot.log") -and
      (Test-CommandLineContains $CommandLine "bot.py"))
}

$nonebotPredicate = {
  param($Process)

  $commandLine = $Process.CommandLine
  $hasRootedBot = Test-CommandLineContains $commandLine $botScript
  $hasRootedLog = (Test-CommandLineContains $commandLine $nonebotLogFile) -and (Test-CommandLineContains $commandLine "bot.py")
  $hasLegacyStartCommand = ($Process.Name -ieq "cmd.exe") -and
    (Test-CommandLineContains $commandLine "UV_CACHE_DIR=.uv-cache") -and
    (Test-CommandLineContains $commandLine "kanamibot.log") -and
    (Test-CommandLineContains $commandLine "bot.py")

  return $hasRootedBot -or $hasRootedLog -or $hasLegacyStartCommand
}

$napcatValidator = {
  param([AllowNull()][string]$CommandLine)

  return (Test-CommandLineContains $CommandLine $napcatWorkDir) -or
    (Test-CommandLineContains $CommandLine $napcatLogFile)
}

$napcatPredicate = {
  param($Process)

  $commandLine = $Process.CommandLine
  return (Test-CommandLineContains $commandLine $napcatWorkDir) -or
    (Test-CommandLineContains $commandLine $napcatLogFile)
}

Stop-MatchedProcesses `
  -Label "NoneBot backend" `
  -PidFiles $nonebotPidFiles `
  -PidValidator $nonebotValidator `
  -Predicate $nonebotPredicate

if (-not $NoneBotOnly) {
  Stop-MatchedProcesses `
    -Label "NapCat backend" `
    -PidFiles $napcatPidFiles `
    -PidValidator $napcatValidator `
    -Predicate $napcatPredicate
}
