# KanamiBot

A lightweight Python QQ bot skeleton built on NoneBot2 and the OneBot v11 adapter.
NapCat connects to this bot through reverse WebSocket.

## Stack

- Python 3.12
- uv
- NoneBot2
- nonebot-adapter-onebot
- NapCat / OneBot v11

## Setup

Install Python dependencies:

```bash
UV_CACHE_DIR=.uv-cache uv sync
```

Download NapCat from GitHub Releases.

macOS:

```bash
./vendor/install_napcat_macos.sh
```

Windows PowerShell:

```powershell
.\vendor\install_napcat_windows.ps1
```

Windows Command Prompt:

```cmd
vendor\install_napcat_windows.cmd
```

Create local configuration:

```bash
cp .env.example .env
```

Edit `.env` and set a private `ONEBOT_ACCESS_TOKEN`. Use the same value in
NapCat's WebSocket Client `token`.

Start the KanamiBot / NoneBot backend:

```bash
./start.sh
```

By default, KanamiBot listens on `127.0.0.1:8280`. If you changed `PORT` in
`.env`, use that port in NapCat's WebSocket Client URL.

Start the NapCat backend after downloading it:

macOS:

```bash
./vendor/start_kanamibot.sh
```

Windows:

```cmd
vendor\start_kanamibot.cmd
```

Despite the historical filename, `vendor/start_kanamibot.*` starts NapCat, not
the NoneBot backend. NapCat's WebUI normally opens at
`http://127.0.0.1:6099/webui/`.

## NapCat Configuration

Local config files have been prepared:

- NoneBot env: `.env`
- NapCat OneBot config: `files/napcat_config/onebot11.json`

In NapCat WebUI, add a network configuration:

- Type: WebSocket Client
- URL: `ws://127.0.0.1:8280/onebot/v11/ws`
- Token: same as `ONEBOT_ACCESS_TOKEN`
- Message format: array

You can also use `files/napcat_config/onebot11.json.example` as a reference.
NapCat's management WebUI is a separate service; after NapCat starts it normally
opens at `http://127.0.0.1:6099/webui/`.

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

```bash
UV_CACHE_DIR=.uv-cache uv run python -m compileall bot.py src
```

Run linting:

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
```
