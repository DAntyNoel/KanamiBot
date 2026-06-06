param(
  [string]$ProjectRoot = "",
  [string]$WorkDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
  $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
  $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
}

function Read-DotEnv {
  param([string]$Path)

  $values = @{}
  if (-not (Test-Path -LiteralPath $Path)) {
    return $values
  }

  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }

    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
      continue
    }

    $key = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    if (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    $values[$key] = $value
  }

  return $values
}

function Get-Setting {
  param(
    [hashtable]$Values,
    [string]$Name,
    [string]$Default
  )

  if ($Values.ContainsKey($Name) -and $Values[$Name]) {
    return $Values[$Name]
  }

  $processValue = [Environment]::GetEnvironmentVariable($Name)
  if ($processValue) {
    return $processValue
  }

  return $Default
}

function Resolve-ProjectPath {
  param(
    [string]$Path,
    [string]$BasePath
  )

  if ([System.IO.Path]::IsPathRooted($Path)) {
    return $Path
  }

  return Join-Path $BasePath $Path
}

function Read-JsonObjectAsHashtable {
  param([string]$Path)

  $result = [ordered]@{}
  if (-not (Test-Path -LiteralPath $Path)) {
    return $result
  }

  $json = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
  foreach ($property in $json.PSObject.Properties) {
    $result[$property.Name] = $property.Value
  }

  return $result
}

function Write-Utf8NoBom {
  param(
    [string]$Path,
    [string]$Content
  )

  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content + [Environment]::NewLine, $encoding)
}

$envFile = Join-Path $ProjectRoot ".env"
$settings = Read-DotEnv -Path $envFile

if (-not $WorkDir) {
  $WorkDir = Get-Setting -Values $settings -Name "NAPCAT_WORKDIR" -Default "files\napcat_runtime"
}
$WorkDir = Resolve-ProjectPath -Path $WorkDir -BasePath $ProjectRoot
$configDir = Join-Path $WorkDir "config"

$nonebotHost = Get-Setting -Values $settings -Name "HOST" -Default "127.0.0.1"
$nonebotPort = [int](Get-Setting -Values $settings -Name "PORT" -Default "12706")
$onebotToken = Get-Setting -Values $settings -Name "ONEBOT_ACCESS_TOKEN" -Default "change-me"
$webuiHost = Get-Setting -Values $settings -Name "NAPCAT_WEBUI_HOST" -Default "127.0.0.1"
$webuiPort = [int](Get-Setting -Values $settings -Name "NAPCAT_WEBUI_PORT" -Default "12705")
$webuiToken = Get-Setting -Values $settings -Name "NAPCAT_WEBUI_TOKEN" -Default $onebotToken
$quickAccount = Get-Setting -Values $settings -Name "NAPCAT_QUICK_ACCOUNT" -Default ""

New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$webuiConfigPath = Join-Path $configDir "webui.json"
$webuiConfig = Read-JsonObjectAsHashtable -Path $webuiConfigPath
$webuiConfig["host"] = $webuiHost
$webuiConfig["port"] = $webuiPort
$webuiConfig["token"] = $webuiToken
if (-not $webuiConfig.Contains("loginRate")) {
  $webuiConfig["loginRate"] = 10
}
$webuiConfig["autoLoginAccount"] = $quickAccount
Write-Utf8NoBom -Path $webuiConfigPath -Content ($webuiConfig | ConvertTo-Json -Depth 30)

$onebotConfig = [ordered]@{
  network = [ordered]@{
    httpServers = @()
    httpSseServers = @()
    httpClients = @()
    websocketServers = @()
    websocketClients = @(
      [ordered]@{
        enable = $true
        name = "kanamibot"
        url = "ws://$($nonebotHost):$($nonebotPort)/onebot/v11/ws"
        reportSelfMessage = $false
        messagePostFormat = "array"
        token = $onebotToken
        debug = $false
        heartInterval = 30000
        reconnectInterval = 5000
      }
    )
    plugins = @()
  }
  musicSignUrl = ""
  enableLocalFile2Url = $false
  parseMultMsg = $false
}

$onebotJson = $onebotConfig | ConvertTo-Json -Depth 30
$onebotTargets = New-Object System.Collections.Generic.List[string]
$onebotTargets.Add((Join-Path $configDir "onebot11.json"))
if ($quickAccount) {
  $onebotTargets.Add((Join-Path $configDir "onebot11_$quickAccount.json"))
}
Get-ChildItem -Path $configDir -Filter "onebot11_*.json" -ErrorAction SilentlyContinue |
  ForEach-Object { $onebotTargets.Add($_.FullName) }

$onebotTargets |
  Select-Object -Unique |
  ForEach-Object { Write-Utf8NoBom -Path $_ -Content $onebotJson }

Write-Host "NapCat config prepared."
Write-Host "Work dir: $WorkDir"
Write-Host "WebUI: http://$($webuiHost):$($webuiPort)/webui/"
Write-Host "OneBot reverse WebSocket: ws://$($nonebotHost):$($nonebotPort)/onebot/v11/ws"
