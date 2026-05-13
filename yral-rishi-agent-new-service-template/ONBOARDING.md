# ONBOARDING — yral-rishi-agent-new-service-template

> One-line purpose: **a reading order + first-week checklist for a new maintainer.** Optimized for a non-programmer + ADHD reader (per Rishi's situation): each item is concrete + has a clear "done when ___" line.

## ⭐ Day 1 — get it running locally

Done when: you can `curl http://localhost:8000/openapi.json` and see a response.

1. Clone the monorepo if you haven't: `git clone git@github.com:dolr-ai/yral-rishi-agent.git`
2. `cd yral-rishi-agent-new-service-template`
3. `cp .env.example .env.local` — leave the secret values empty; the service runs in no-Sentry / no-Langfuse mode locally.
4. `docker compose up --build` — this pulls Postgres 17, pgBouncer 1.23, Redis 7 and builds the service image.
5. Hit `http://localhost:8000/openapi.json` — should return a JSON document describing the API.

If anything fails, head to `TROUBLESHOOTING.md` for symptom-to-fix.

## Day 2 — tour the code

Done when: you can answer "what runs when a request comes in" in 2 sentences.

1. Read `ARCHITECTURE.md` end-to-end (5 minutes).
2. Read `WALKTHROUGH.md` (a narrative trace of one request, file-by-file). This is the fastest path to a mental model.
3. Skim `app/main.py` — note the import order at top: Sentry init → Langfuse init → logging config → app construction → middleware mount. That order is load-bearing; the file header explains why.
4. Look at `app/sentry_middleware.py` first (it's the smallest), then `langfuse_middleware.py`, then `request_id_middleware.py`, then `logging.py`, then `config.py`.

## Day 3 — read the constraints

Done when: you know what A2.1, A7, B7, D8, F12, F16, and H6 say without looking them up.

These are the constraints you'll bump into MOST often:

- **A2.1** — avoid over-engineering. Ask before adding new abstractions / dependencies / multi-step workflows.
- **A7** — Sentry = `sentry.rishi.yral.com`. Never `apm.yral.com`. Reinforced 3 times.
- **B7** — every file gets a 3-tier doc structure: file header → function WHAT/WHEN/WHY → role-not-syntax line comments.
- **D8** — per-service `secrets.yaml` declares every secret the service needs.
- **F12** — Python 3.12 + FastAPI + asyncio + asyncpg across all 13 v2 services.
- **F16** — monorepo. Spawned services are SUBFOLDERS, not new repos.
- **H6** — PII never in Loki / Sentry / Langfuse. The structured logger enforces an allowlist.

Source of truth: `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`.

## Day 4-5 — first PR

Done when: you've opened a small PR and it's gone through Codex review (per I10).

Suggestions for a first PR (low-risk):
- Add a field to `_FIELD_ALLOWLIST` in `app/logging.py` that you noticed a log line wanting.
- Add a missing entry to `GLOSSARY.md`.
- Improve an unclear comment somewhere.

Avoid for a first PR: middleware refactors, dependency bumps, anything in `app/main.py`'s import-order section.

## Where to ask for help

- Slack / Google Chat (links go here once Day 5-6 fills in real content).
- The coordinator session (Rishi + Claude) — for cross-cutting questions.
- `WHEN-YOU-GET-LOST.md` — for "I don't even know what I don't know" days.

## RELATED FILES

- `README.md` — entrypoint + doc index
- `ARCHITECTURE.md` — system overview
- `WALKTHROUGH.md` — narrative trace of a single request
- `GLOSSARY.md` — definitions of every domain term
- `WHEN-YOU-GET-LOST.md` — one-page north-star orientation
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the rules

## Status

Scaffold. Real onboarding tour fills in Days 5-6 once the template is more complete (database + redis + LLM modules, real endpoints).
