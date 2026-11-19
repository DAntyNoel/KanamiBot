param(
    [string]$Python = (Join-Path $PSScriptRoot '..\..\KanamiBot\.venv\Scripts\python.exe'),
    [ValidateRange(1, 65535)][int]$Port = 12766,
    [switch]$Sandbox
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "端口 $Port 已被占用；不会自动停止现有服务。"
}
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$arguments = @('-u', 'qq_sandbox.py', '--port', "$Port")
if (-not $Sandbox) {
    $arguments += '--production'
}
$previousPath = $env:PYTHONPATH
$previousEncoding = $env:PYTHONIOENCODING
try {
    $env:PYTHONPATH = Join-Path $root 'src'
    $env:PYTHONIOENCODING = 'utf-8'
    $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'qq_sandbox.stdout.log') `
        -RedirectStandardError (Join-Path $logs 'qq_sandbox.stderr.log')
    Write-Output "QQBot 已创建启动进程 PID=$($process.Id)，端口=$Port；请检查日志确认连接成功。"
} finally {
    $env:PYTHONPATH = $previousPath
    $env:PYTHONIOENCODING = $previousEncoding
}
