# Vendor Startup

## Install NapCat Shell

NapCat Shell is not tracked as a git submodule. Windows uses NapCat Shell under
`vendor/NapCat.Shell/`. By default, the installer links the local shell
directory from `D:\DAntyNoel\NapCat.Shell` when it exists, or from
`NAPCAT_SHELL_DIR` / `-ShellSourceDir` when provided:

```powershell
.\vendor\install_napcat_windows.ps1
```

Command Prompt wrapper:

```cmd
vendor\install_napcat_windows.cmd
```

Download a Shell release directly into `vendor/NapCat.Shell/` if needed:

```powershell
.\vendor\install_napcat_windows.ps1 -Download
```

Set a specific release tag if needed while downloading:

```powershell
.\vendor\install_napcat_windows.ps1 -Download -Version v4.18.4
```

## Configure NapCat

NapCat uses a project-local work directory:

```text
files/napcat_runtime/
```

Run the config sync manually if needed:

```powershell
.\vendor\configure_napcat_windows.ps1
```

It reads `.env` and writes:

- `files/napcat_runtime/config/webui.json`
- `files/napcat_runtime/config/onebot11.json`

Default local endpoints:

```text
http://127.0.0.1:12705/webui/
ws://127.0.0.1:12706/onebot/v11/ws
```

## Start NapCat

These scripts live under `vendor/` and start only the NapCat backend. The
filename is historical; they do not start the KanamiBot / NoneBot process.

Start NapCat on Windows Command Prompt:

```cmd
vendor\start_kanamibot.cmd
```

Start NapCat on Windows PowerShell:

```powershell
.\vendor\start_kanamibot.ps1
```

Use the root `start.cmd` or `start.ps1` scripts to start both NapCat and
NoneBot in one step.

## Mock NapCat Backend

`vendor/mock_napcat/` is an independent cross-platform OneBot v11 mock service.
It can replace NapCat for local backend validation before production rollout.
It does not log in to QQ and does not import NoneBot; it only connects to the
backend reverse WebSocket and answers common OneBot API calls.

Start KanamiBot first:

```powershell
uv run python bot.py
```

Start the mock service in another terminal:

```powershell
uv run --project vendor/mock_napcat mock-napcat service
```

Or use the wrapper scripts:

```powershell
.\vendor\start_mock_napcat.ps1
```

```cmd
vendor\start_mock_napcat.cmd
```

```sh
sh vendor/start_mock_napcat.sh
```

Send a test group message:

```powershell
uv run --project vendor/mock_napcat mock-napcat send --group 10000 --user 123456789 "ping"
```

Run end-to-end smoke checks while KanamiBot and the mock service are running:

```powershell
uv run --project vendor/mock_napcat mock-napcat smoke
```

Wrapper scripts are also available:

```powershell
.\vendor\check_mock_napcat.ps1
```

```cmd
vendor\check_mock_napcat.cmd
```

```sh
sh vendor/check_mock_napcat.sh
```

The mock service reads defaults from `.env.example`, overrides them with `.env`
from the project root, then applies process environment variables. Its local
control socket binds to `127.0.0.1:12716` by default and is intended only for
local validation.
