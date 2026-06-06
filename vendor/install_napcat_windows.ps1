param(
  [string]$Version = "latest",
  [string]$AssetName = "NapCat.Shell.Windows.Node.zip",
  [string]$ShellSourceDir = $env:NAPCAT_SHELL_DIR,
  [switch]$Download
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$vendorDir = Join-Path $projectRoot "vendor"
$installDir = Join-Path $vendorDir "NapCat.Shell"
$downloadDir = Join-Path $projectRoot "vendor\.napcat-download"
$releaseJson = Join-Path $downloadDir "release.json"
$defaultLocalShellDir = "D:\DAntyNoel\NapCat.Shell"

function Remove-InstallPath {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    Remove-Item -LiteralPath $Path -Force
    return
  }

  Remove-Item -LiteralPath $Path -Recurse -Force
}

function New-ShellDirectoryLink {
  param(
    [string]$SourceDir,
    [string]$LinkDir
  )

  $resolvedSource = (Resolve-Path -LiteralPath $SourceDir).Path

  if (Test-Path -LiteralPath $LinkDir) {
    $item = Get-Item -LiteralPath $LinkDir -Force
    $currentTarget = $null
    if ($item.PSObject.Properties.Name -contains "Target") {
      $currentTarget = @($item.Target) | Select-Object -First 1
    }

    if ($currentTarget) {
      try {
        if ((Resolve-Path -LiteralPath $currentTarget).Path -eq $resolvedSource) {
          Write-Host "NapCat Shell is already linked."
          Write-Host "Source dir: $resolvedSource"
          Write-Host "Vendor dir: $LinkDir"
          return
        }
      } catch {
        # Replace stale links below.
      }
    }

    Remove-InstallPath -Path $LinkDir
  }

  New-Item -ItemType Junction -Path $LinkDir -Target $resolvedSource | Out-Null
  Write-Host "NapCat Shell linked into vendor."
  Write-Host "Source dir: $resolvedSource"
  Write-Host "Vendor dir: $LinkDir"
}

New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null

if (-not $Download) {
  if (-not $ShellSourceDir -and (Test-Path -LiteralPath $defaultLocalShellDir)) {
    $ShellSourceDir = $defaultLocalShellDir
  }

  if ($ShellSourceDir) {
    if (-not (Test-Path -LiteralPath $ShellSourceDir)) {
      throw "NapCat Shell source directory does not exist: $ShellSourceDir. Use -Download to install from GitHub Releases."
    }

    New-ShellDirectoryLink -SourceDir $ShellSourceDir -LinkDir $installDir
    Write-Host "NapCat WebUI normally listens on http://127.0.0.1:6099/webui/ after NapCat starts."
    exit 0
  }
}

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

if ($Version -eq "latest") {
  $apiUrl = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
} else {
  $apiUrl = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/tags/$Version"
}

Write-Host "Fetching NapCat release metadata: $Version"
$headers = @{
  "Accept" = "application/vnd.github+json"
  "User-Agent" = "KanamiBot-NapCat-Installer"
}
$release = Invoke-RestMethod -Uri $apiUrl -Headers $headers
$release | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -Path $releaseJson

$asset = $release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
if (-not $asset) {
  $names = ($release.assets | ForEach-Object { $_.name }) -join ", "
  throw "Asset `"$AssetName`" was not found. Available assets: $names"
}

$archive = Join-Path $downloadDir $AssetName
Write-Host "Downloading $AssetName from $($release.tag_name)"
Invoke-WebRequest -Uri $asset.browser_download_url -Headers @{ "User-Agent" = "KanamiBot-NapCat-Installer" } -OutFile $archive

Write-Host "Installing into $installDir"
Remove-InstallPath -Path $installDir
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Expand-Archive -Path $archive -DestinationPath $installDir -Force

Write-Host "NapCat $($release.tag_name) installed."
Write-Host "Windows asset: $AssetName"
Write-Host "Install dir: $installDir"
Write-Host "NapCat WebUI normally listens on http://127.0.0.1:6099/webui/ after NapCat starts."
