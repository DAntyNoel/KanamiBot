# KanamiBot

A lightweight Python QQ bot skeleton built on NoneBot2 and the OneBot v11 adapter.
NapCat connects to this bot through reverse WebSocket.

## Stack

- Python 3.12
- uv
- NoneBot2
- nonebot-adapter-onebot
- NapCat Shell / OneBot v11

## Setup

Install Python dependencies:

```powershell
$env:UV_CACHE_DIR=".uv-cache"; uv sync
```

Link or install NapCat Shell:

```powershell
.\vendor\install_napcat_windows.ps1
```

Command Prompt wrapper:

```cmd
vendor\install_napcat_windows.cmd
```

Create local configuration:

```powershell
Copy-Item .env.example .env
```

The prepared local ports are:

- NapCat WebUI: `http://127.0.0.1:12705/webui/`
- NoneBot OneBot reverse WebSocket: `ws://127.0.0.1:12706/onebot/v11/ws`

Set a private `ONEBOT_ACCESS_TOKEN` in `.env`. The startup script writes the
same token into NapCat's generated OneBot config before launching NapCat.

## Start

Use one root script to start both NapCat Shell and the NoneBot backend:

```powershell
.\start.ps1
```

or:

```cmd
start.cmd
```

NapCat uses `files/napcat_runtime/` as its isolated work directory through
`NAPCAT_WORKDIR`, so this project does not reuse the installed Shell directory's
existing runtime config. Runtime logs are written to `logs/napcat.log` and
`logs/kanamibot.log`.

The historical `vendor/start_kanamibot.*` scripts start only NapCat Shell. Use
the root `start.*` scripts for one-click startup.

## NapCat Configuration

The startup scripts generate NapCat config from `.env`:

- WebUI config: `files/napcat_runtime/config/webui.json`
- OneBot config: `files/napcat_runtime/config/onebot11.json`

Reference templates live in:

- `files/napcat_config/webui.json.example`
- `files/napcat_config/onebot11.json.example`

## Smoke Test

After NapCat connects successfully, send one of these messages in QQ:

```text
/ping
ping
```

KanamiBot should reply:

```text
pong
```

Echo test:

```text
/echo hello
echo hello
```

KanamiBot should reply:

```text
hello
```

## Development

Compile-check local code:

```powershell
$env:UV_CACHE_DIR=".uv-cache"; uv run python -m compileall bot.py src
```

Run linting:

```powershell
$env:UV_CACHE_DIR=".uv-cache"; uv run ruff check .
```
