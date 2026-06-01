#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOWNLOAD_DIR="$PROJECT_ROOT/vendor/.napcat-download"
INSTALL_DIR="${NAPCAT_MAC_INSTALLER_DIR:-$PROJECT_ROOT/vendor}"
VERSION="${NAPCAT_MAC_INSTALLER_VERSION:-latest}"
REQUESTED_ASSET="${NAPCAT_MAC_INSTALLER_ASSET_NAME:-}"
OPEN_AFTER_INSTALL="${NAPCAT_MAC_INSTALLER_OPEN:-1}"
MIN_MACOS_VERSION="12.0"
RELEASE_REPO="NapNeko/NapCat-Mac-Installer"
USER_AGENT="KanamiBot-NapCat-Mac-Installer"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS. Use vendor/install_napcat_windows.ps1 on Windows." >&2
  exit 1
fi

for command_name in curl python3 ditto sw_vers; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required." >&2
    exit 1
  fi
done

MACOS_VERSION="$(sw_vers -productVersion)"
python3 - "$MACOS_VERSION" "$MIN_MACOS_VERSION" <<'PY'
import sys


def version_tuple(value):
    parts = [int(part) for part in value.split(".")[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


current, minimum = sys.argv[1], sys.argv[2]
if version_tuple(current) < version_tuple(minimum):
    raise SystemExit(
        f"NapCat.MacOs requires macOS {minimum} or later. Current macOS: {current}"
    )
PY

mkdir -p "$DOWNLOAD_DIR"
RELEASE_JSON="$DOWNLOAD_DIR/napcat-macos-release.json"

if [[ "$VERSION" == "latest" ]]; then
  API_URL="https://api.github.com/repos/$RELEASE_REPO/releases/latest"
else
  API_URL="https://api.github.com/repos/$RELEASE_REPO/releases/tags/$VERSION"
fi

echo "Fetching NapCat.MacOs release metadata: $VERSION"
curl -fsSL \
  -H "Accept: application/vnd.github+json" \
  -H "User-Agent: $USER_AGENT" \
  "$API_URL" \
  -o "$RELEASE_JSON"

SELECTED_ASSET="$(
  python3 - "$RELEASE_JSON" "$REQUESTED_ASSET" <<'PY'
import json
import sys

release_path, requested_name = sys.argv[1], sys.argv[2]
with open(release_path, "r", encoding="utf-8") as file:
    release = json.load(file)

assets = release.get("assets", [])
if requested_name:
    for asset in assets:
        if asset.get("name") == requested_name:
            print(
                release["tag_name"],
                asset["name"],
                asset["browser_download_url"],
                sep="\t",
            )
            break
    else:
        names = ", ".join(asset.get("name", "") for asset in assets)
        raise SystemExit(
            f'Asset "{requested_name}" was not found. Available assets: {names}'
        )
else:
    scored_assets = []
    for asset in assets:
        name = asset.get("name", "")
        lower_name = name.lower()
        if not lower_name.endswith((".dmg", ".zip")):
            continue

        score = 0
        if lower_name.endswith(".dmg"):
            score += 40
        if lower_name.endswith(".zip"):
            score += 20
        if "napcat" in lower_name:
            score += 30
        if "mac" in lower_name or "macos" in lower_name or "darwin" in lower_name:
            score += 20
        if "installer" in lower_name:
            score += 10
        scored_assets.append((score, name, asset))

    if not scored_assets:
        names = ", ".join(asset.get("name", "") for asset in assets)
        raise SystemExit(
            "No macOS installer asset was found. "
            "Set NAPCAT_MAC_INSTALLER_ASSET_NAME explicitly. "
            f"Available assets: {names}"
        )

    scored_assets.sort(reverse=True)
    _, _, asset = scored_assets[0]
    print(release["tag_name"], asset["name"], asset["browser_download_url"], sep="\t")
PY
)"

IFS=$'\t' read -r TAG_NAME ASSET_NAME DOWNLOAD_URL <<<"$SELECTED_ASSET"
ARCHIVE="$DOWNLOAD_DIR/$ASSET_NAME"
ASSET_NAME_LOWER="$(printf '%s' "$ASSET_NAME" | tr '[:upper:]' '[:lower:]')"
STAGE_DIR="$DOWNLOAD_DIR/stage"
MOUNT_DIR="$DOWNLOAD_DIR/mount"
MOUNTED_DMG=0
TARGET_APP=""

cleanup() {
  if [[ "$MOUNTED_DMG" == "1" ]]; then
    hdiutil detach "$MOUNT_DIR" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$STAGE_DIR" "$MOUNT_DIR"
}
trap cleanup EXIT

find_app_bundle() {
  python3 - "$1" <<'PY'
import os
import sys

root = sys.argv[1]
for current_root, dirnames, _ in os.walk(root):
    for dirname in dirnames:
        if dirname.endswith(".app"):
            print(os.path.join(current_root, dirname))
            raise SystemExit(0)
raise SystemExit(1)
PY
}

install_app_bundle() {
  local source_app="$1"
  local target_name

  target_name="${NAPCAT_MAC_INSTALLER_APP_NAME:-$(basename "$source_app")}"
  TARGET_APP="$INSTALL_DIR/$target_name"

  mkdir -p "$INSTALL_DIR"
  echo "Installing $target_name into $INSTALL_DIR"
  rm -rf "$TARGET_APP"
  ditto "$source_app" "$TARGET_APP"
}

echo "Downloading $ASSET_NAME from $TAG_NAME"
curl -fL \
  -H "User-Agent: $USER_AGENT" \
  "$DOWNLOAD_URL" \
  -o "$ARCHIVE"

case "$ASSET_NAME_LOWER" in
  *.dmg)
    if ! command -v hdiutil >/dev/null 2>&1; then
      echo "hdiutil is required to install a DMG asset." >&2
      exit 1
    fi

    rm -rf "$MOUNT_DIR"
    mkdir -p "$MOUNT_DIR"
    hdiutil attach "$ARCHIVE" -nobrowse -readonly -mountpoint "$MOUNT_DIR" -quiet
    MOUNTED_DMG=1

    SOURCE_APP="$(find_app_bundle "$MOUNT_DIR")" || {
      echo "No .app bundle was found in $ASSET_NAME." >&2
      exit 1
    }
    install_app_bundle "$SOURCE_APP"
    ;;
  *.zip)
    if ! command -v unzip >/dev/null 2>&1; then
      echo "unzip is required to install a ZIP asset." >&2
      exit 1
    fi

    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"
    unzip -q "$ARCHIVE" -d "$STAGE_DIR"

    SOURCE_APP="$(find_app_bundle "$STAGE_DIR")" || {
      echo "No .app bundle was found in $ASSET_NAME." >&2
      exit 1
    }
    install_app_bundle "$SOURCE_APP"
    ;;
  *)
    echo "Unsupported asset type: $ASSET_NAME" >&2
    exit 1
    ;;
esac

echo "NapCat.MacOs installer $TAG_NAME installed."
echo "Installer app: $TARGET_APP"

if [[ "$OPEN_AFTER_INSTALL" != "0" ]]; then
  echo "Opening NapCat.MacOs installer..."
  open "$TARGET_APP"
else
  echo "Open it manually when ready: open \"$TARGET_APP\""
fi

echo "Use the installer app to download/update NapCatQQ, select QQ, and start NapCat."
echo "NapCat WebUI normally listens on http://127.0.0.1:6099/webui/ after NapCat starts."
