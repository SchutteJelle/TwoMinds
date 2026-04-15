# TwoMinds

TwoMinds is a small Express app where two AI personas discuss a topic in alternating turns.

## New: join the discussion

During a running conversation you can click `Praat mee`, type your own message, and send it with the button or `Cmd/Ctrl + Enter`.
Your message is queued and injected before the next AI turn.

## Requirements

- Node.js 18+
- An Anthropic API key

## Install

```bash
npm install
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
npm run start
```

Or run with auto-restart during development:

```bash
npm run dev
```

Open:

- http://localhost:3000

If port 3000 is already in use, start on another port:

```bash
PORT=3001 npm run start
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
