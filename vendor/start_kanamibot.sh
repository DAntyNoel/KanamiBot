#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${NAPCAT_MAC_INSTALLER_DIR:-$PROJECT_ROOT/vendor}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This launcher is for macOS. Use vendor/start_kanamibot.ps1 or vendor/start_kanamibot.cmd on Windows." >&2
  exit 1
fi

if ! command -v open >/dev/null 2>&1; then
  echo "open is required to launch the NapCat.MacOs installer app." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to locate the NapCat.MacOs installer app." >&2
  exit 1
fi

find_installer_app() {
  python3 - "$INSTALL_DIR" "/Applications" <<'PY'
import os
import sys

roots = []
for root in sys.argv[1:]:
    if root and os.path.isdir(root) and root not in roots:
        roots.append(root)

preferred_names = (
    "NapCatInstaller.app",
    "NapCat-Mac-Installer.app",
    "NapCat.MacOs.app",
)

for root in roots:
    for name in preferred_names:
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate):
            print(candidate)
            raise SystemExit(0)

for root in roots:
    for current_root, dirnames, _ in os.walk(root):
        for dirname in dirnames:
            lower_name = dirname.lower()
            if dirname.endswith(".app") and "napcat" in lower_name:
                print(os.path.join(current_root, dirname))
                raise SystemExit(0)
raise SystemExit(1)
PY
}

INSTALLER_APP="$(find_installer_app)" || {
  echo "NapCat.MacOs installer app is not installed. Run ./vendor/install_napcat_macos.sh first." >&2
  exit 1
}

echo "Opening NapCat.MacOs installer app..."
echo "App: $INSTALLER_APP"
echo "After NapCat starts, WebUI normally listens on http://127.0.0.1:6099/webui/"

if [[ "$#" -gt 0 ]]; then
  exec open "$INSTALLER_APP" --args "$@"
else
  exec open "$INSTALLER_APP"
fi
