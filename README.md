# yral-rishi-agent

AI chat service for the YRAL app. Runs at agent.rishi.yral.com.

Replaces [yral-chat-ai](https://github.com/dolr-ai/yral-chat-ai) with the same API contract, targeting 50% lower latency.

## Quick start

```bash
cp .env.example .env
# Fill in your secrets
pip install -r requirements.txt
cd app && uvicorn main:app --reload
```

## Architecture

```
Mobile app → Caddy (rishi-1/2) → FastAPI (rishi-4/5) → Patroni Postgres (rishi-4/5/6)
```

See [CLAUDE.md](CLAUDE.md) for code patterns and rules.
