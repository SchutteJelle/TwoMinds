# TwoMinds

TwoMinds is a small FastAPI app where two AI personas discuss a topic in alternating turns.

## New: join the discussion

During a running conversation you can click `Praat mee`, type your own message, and send it with the button or `Cmd/Ctrl + Enter`.
Your message is queued and injected before the next AI turn.

## Requirements

- Python 3.10+
- An Anthropic API key

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure API key

Set your key before starting the server:

```bash
export ANTHROPIC_API_KEY="your_key_here"
```

Tip: put this in your shell profile (`~/.zshrc`) if you use it often.

## Run

Start normally:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 3000
```

Or run with auto-restart during development:

```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 3000
```

Open:

- http://localhost:3000

If port 3000 is already in use, start on another port:

```bash
PORT=3001 python -m uvicorn main:app --host 0.0.0.0 --port 3001
```

## Health check

```bash
curl http://localhost:3000/api/health
```

Expected response:

```json
{"ok":true,"configured":true}
```

If `configured` is `false`, the UI loads but `/api/converse` returns `503` until `ANTHROPIC_API_KEY` is set.

## Logging

The backend logs structured JSON events for:

- startup and configuration state
- session lifecycle
- turn start and completion
- user message queueing
- stream errors/disconnects

Set verbosity with:

```bash
LOG_LEVEL=DEBUG python -m uvicorn main:app --host 0.0.0.0 --port 3000
```
