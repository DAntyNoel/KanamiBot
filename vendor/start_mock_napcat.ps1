$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$mockProject = Join-Path $projectRoot "vendor\mock_napcat"
Set-Location $projectRoot

$env:UV_CACHE_DIR = if ($env:UV_CACHE_DIR) { $env:UV_CACHE_DIR } else { ".uv-cache" }
uv run --project $mockProject mock-napcat service @args
exit $LASTEXITCODE
