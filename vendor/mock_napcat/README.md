# Mock NapCat

Mock NapCat is a small, independent OneBot v11 reverse WebSocket service. It
can replace NapCat during local backend validation without logging in to QQ.

It does not import NoneBot. It only connects to the bot backend through:

```text
ws://HOST:PORT/onebot/v11/ws
```

Configuration defaults are read from the repository root `.env.example`, then
overridden by `.env`, then process environment variables.

## Run

Start KanamiBot first:

```powershell
uv run python bot.py
```

Start the mock service:

```powershell
uv run --project vendor/mock_napcat mock-napcat service
```

In another terminal, send a group message:

```powershell
uv run --project vendor/mock_napcat mock-napcat send --group 10000 --user 123456789 "ping"
```

Run smoke validation:

```powershell
uv run --project vendor/mock_napcat mock-napcat smoke
```

## Commands

- `service`: connect to NoneBot as a OneBot v11 reverse WebSocket client and
  open a local control socket.
- `send`: inject a private or group message through the running service.
- `smoke`: run end-to-end checks against the running service.
- `history`: show recent bot messages captured by the mock service.
- `calls`: show recent OneBot API calls received from the bot backend.

The control socket binds to `127.0.0.1` by default and is intended for local
validation only.
