param(
  [string]$Version = "latest",
  [string]$AssetName = "NapCat.Shell.Windows.Node.zip"
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$installDir = Join-Path $projectRoot "vendor\NapCatQQ"
$downloadDir = Join-Path $projectRoot "vendor\.napcat-download"
$releaseJson = Join-Path $downloadDir "release.json"

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
if (Test-Path $installDir) {
  Remove-Item -Recurse -Force $installDir
}
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Expand-Archive -Path $archive -DestinationPath $installDir -Force

Write-Host "NapCat $($release.tag_name) installed."
Write-Host "Windows asset: $AssetName"
Write-Host "Install dir: $installDir"
Write-Host "NapCat WebUI normally listens on http://127.0.0.1:6099/webui/ after NapCat starts."
