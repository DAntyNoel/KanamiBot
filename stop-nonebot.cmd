@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "KANAMIBOT_STOP_ROOT=%~dp0"
echo [KanamiBot] Checking NoneBot on port 12706...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "try {" ^
  "  $root = $env:KANAMIBOT_STOP_ROOT;" ^
  "  $expectedPython = Join-Path $root '.venv\Scripts\python.exe';" ^
  "  $botArguments = '\s+(?:-u\s+|-X\s+utf8(?:=[01])?\s+)*[\x22]?(?:bot\.py|' + [regex]::Escape((Join-Path $root 'bot.py')) + ')[\x22]?(?=\s|$)';" ^
  "  $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 12706 } | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "  if ($listeners.Count -eq 0) { Write-Host '[KanamiBot] NoneBot is not running on port 12706.'; exit 0 };" ^
  "  $targets = @();" ^
  "  foreach ($listenerId in $listeners) {" ^
  "    $backend = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listenerId);" ^
  "    if (-not $backend) { throw 'The listener exited during inspection. Run this script again.' };" ^
  "    $backendCommand = '^[\x22]?' + [regex]::Escape($backend.ExecutablePath) + '[\x22]?' + $botArguments;" ^
  "    $wrapperCommand = '^[\x22]?' + [regex]::Escape($expectedPython) + '[\x22]?' + $botArguments;" ^
  "    $wrapper = $backend;" ^
  "    if ($backend.ExecutablePath -ne $expectedPython) { $wrapper = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $backend.ParentProcessId) };" ^
  "    if ($backend.Name -ne 'python.exe' -or $backend.CommandLine -notmatch $backendCommand -or -not $wrapper -or $wrapper.ExecutablePath -ne $expectedPython -or $wrapper.CommandLine -notmatch $wrapperCommand -or $wrapper.CreationDate -gt $backend.CreationDate) {" ^
  "      throw ('Refusing to stop PID ' + $listenerId + ': port 12706 is not owned by this project''s NoneBot.')" ^
  "    };" ^
  "    $targets += $backend;" ^
  "    if ($wrapper.ProcessId -ne $backend.ProcessId) { $targets += $wrapper };" ^
  "  };" ^
  "  foreach ($target in $targets) {" ^
  "    $current = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $target.ProcessId);" ^
  "    if (-not $current) { continue };" ^
  "    if ($current.CreationDate -ne $target.CreationDate -or $current.ExecutablePath -ne $target.ExecutablePath -or $current.CommandLine -ne $target.CommandLine) { throw 'Process identity changed. No further processes will be stopped.' };" ^
  "    try { Stop-Process -Id $current.ProcessId -Force -ErrorAction Stop } catch { if (Get-Process -Id $current.ProcessId -ErrorAction SilentlyContinue) { throw } };" ^
  "    Write-Host ('[KanamiBot] Stopped NoneBot PID ' + $current.ProcessId + '.');" ^
  "  };" ^
  "  Wait-Process -Id @($targets.ProcessId) -Timeout 5 -ErrorAction SilentlyContinue;" ^
  "  $remaining = @(Get-Process -Id @($targets.ProcessId) -ErrorAction SilentlyContinue);" ^
  "  $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 12706 });" ^
  "  if ($remaining.Count -gt 0 -or $listeners.Count -gt 0) { throw 'NoneBot is still running or port 12706 is still in use.' };" ^
  "  Write-Host '[KanamiBot] NoneBot stopped. Port 12706 is free. NapCat was left running.';" ^
  "  exit 0;" ^
  "} catch { Write-Host ('[KanamiBot] ERROR: ' + $_.Exception.Message); exit 1 }"

set "STOP_EXIT_CODE=%ERRORLEVEL%"
if not "%STOP_EXIT_CODE%"=="0" pause
exit /b %STOP_EXIT_CODE%
