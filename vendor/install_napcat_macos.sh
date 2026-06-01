#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="$PROJECT_ROOT/vendor/NapCatQQ"
DOWNLOAD_DIR="$PROJECT_ROOT/vendor/.napcat-download"
VERSION="${NAPCAT_VERSION:-latest}"
ASSET_NAME="${NAPCAT_ASSET_NAME:-NapCat.Shell.zip}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. Use vendor/install_napcat_windows.ps1 on Windows." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if ! command -v unzip >/dev/null 2>&1; then
  echo "unzip is required." >&2
  exit 1
fi

mkdir -p "$DOWNLOAD_DIR"
RELEASE_JSON="$DOWNLOAD_DIR/release.json"

if [[ "$VERSION" == "latest" ]]; then
  API_URL="https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
else
  API_URL="https://api.github.com/repos/NapNeko/NapCatQQ/releases/tags/$VERSION"
fi

echo "Fetching NapCat release metadata: $VERSION"
curl -fsSL -H "Accept: application/vnd.github+json" -H "User-Agent: KanamiBot-NapCat-Installer" \
  "$API_URL" -o "$RELEASE_JSON"

read -r TAG_NAME DOWNLOAD_URL < <(
  python3 - "$RELEASE_JSON" "$ASSET_NAME" <<'PY'
import json
import sys

release_path, asset_name = sys.argv[1], sys.argv[2]
with open(release_path, "r", encoding="utf-8") as file:
    release = json.load(file)

for asset in release.get("assets", []):
    if asset.get("name") == asset_name:
        print(release["tag_name"], asset["browser_download_url"])
        break
else:
    names = ", ".join(asset.get("name", "") for asset in release.get("assets", []))
    raise SystemExit(f'Asset "{asset_name}" was not found. Available assets: {names}')
PY
)

ARCHIVE="$DOWNLOAD_DIR/$ASSET_NAME"
echo "Downloading $ASSET_NAME from $TAG_NAME"
curl -fL -H "User-Agent: KanamiBot-NapCat-Installer" "$DOWNLOAD_URL" -o "$ARCHIVE"

echo "Installing into $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
unzip -q "$ARCHIVE" -d "$INSTALL_DIR"

echo "NapCat $TAG_NAME installed."
echo "macOS asset: $ASSET_NAME"
echo "Install dir: $INSTALL_DIR"
echo "NapCat WebUI normally listens on http://127.0.0.1:6099/webui/ after NapCat starts."
