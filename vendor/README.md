# Vendor Startup

## Download NapCat

NapCat is downloaded from GitHub Releases and is not tracked as a git submodule.
Downloaded files are installed into `vendor/NapCatQQ/`.

macOS downloads the cross-platform shell asset:

```bash
./vendor/install_napcat_macos.sh
```

Windows downloads the Windows Node asset:

```powershell
.\vendor\install_napcat_windows.ps1
```

Command Prompt wrapper:

```cmd
vendor\install_napcat_windows.cmd
```

Set a specific release tag if needed:

```bash
NAPCAT_VERSION=v4.18.4 ./vendor/install_napcat_macos.sh
```

```powershell
.\vendor\install_napcat_windows.ps1 -Version v4.18.4
```

NapCat WebUI normally listens on:

```text
http://127.0.0.1:6099/webui/
```

KanamiBot's OneBot reverse WebSocket endpoint is separate from the WebUI.

## Start NapCat

These scripts live under `vendor/` and start the NapCat backend. The filename is
historical; they do not start the KanamiBot / NoneBot process.

Start NapCat on macOS:

```bash
./vendor/start_kanamibot.sh
```

Start NapCat on Windows Command Prompt:

```cmd
vendor\start_kanamibot.cmd
```

Start NapCat on Windows PowerShell:

```powershell
.\vendor\start_kanamibot.ps1
```

NapCat WebSocket Client:

```text
ws://127.0.0.1:8280/onebot/v11/ws
```

Use the token in `files/napcat_config/onebot11.json`.

The NapCat startup scripts run in the current terminal and print logs directly
there. Press `Ctrl+C` to stop NapCat.

The KanamiBot / NoneBot backend is started from the project root:

```bash
./start.sh
```
