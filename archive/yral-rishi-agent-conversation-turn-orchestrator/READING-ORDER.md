# READING-ORDER — yral-rishi-agent-conversation-turn-orchestrator

> One-line purpose: **the ordered file list a new maintainer should read first, with ETA and priority per file.** Optimized for a non-programmer + ADHD reader: numbered, time-budgeted, priority-marked.

## ⭐ START HERE

Time budget: about **90 minutes** to read everything HIGH priority. Skip MED + LOW your first day; come back to them as needed.

Legend:
- 🟥 HIGH — must-read before touching any code
- 🟨 MED — read after first PR
- ⬜ LOW — reference, read when relevant

| # | File | ETA | Priority | Why you read it |
|---|---|---|---|---|
| 1 | `README.md` | 5 min | 🟥 HIGH | Service summary + quick-start + doc index. Tier-1 entrypoint. |
| 2 | `DEEP-DIVE.md` | 10 min | 🟥 HIGH | ASCII diagrams of request flow, deploy flow, DB HA, network. Builds the mental model. |
| 3 | `WHEN-YOU-GET-LOST.md` | 3 min | 🟥 HIGH | One-page north-star orientation. Bookmark it. |
| 4 | `CLAUDE.md` | 10 min | 🟥 HIGH | Instructions for AI agents working here (Claude Code + Codex). Read even if human. |
| 5 | `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` | 15 min | 🟥 HIGH | The rules every PR must honor. Memorize A2.1, A7, B7, D8, F12, F16, H6. |
| 6 | `GLOSSARY.md` | 5 min | 🟥 HIGH | Plain-English definitions. Skim now, refer back. |
| 7 | `WALKTHROUGH.md` | 15 min | 🟥 HIGH | Narrative trace of one user action through the code. Connects diagrams to source. |
| 8 | `app/main.py` | 5 min | 🟥 HIGH | The FastAPI entry-point — note the import order at top (Sentry → Langfuse → logging → app construction → middleware mount). |
| 9 | `app/sentry_middleware.py` | 3 min | 🟥 HIGH | Smallest middleware. Easiest place to start reading code. |
| 10 | `app/langfuse_middleware.py` | 5 min | 🟥 HIGH | Same shape as Sentry (init / get / flush singleton). |
| 11 | `app/request_id_middleware.py` | 5 min | 🟥 HIGH | ContextVar pattern + Starlette middleware. |
| 12 | `app/logging.py` | 7 min | 🟥 HIGH | structlog + H6 allowlist redaction. |
| 13 | `app/config.py` | 5 min | 🟥 HIGH | pydantic-settings typed Settings + `get_settings()` singleton. |
| 14 | `SECURITY.md` | 10 min | 🟨 MED | Threat model. Read before working in auth / billing / LLM-routing code. |
| 15 | `RUNBOOK.md` | 10 min | 🟨 MED | Operating procedures. Read before first prod deploy you're involved in. |
| 16 | `Dockerfile` | 3 min | 🟨 MED | Multi-stage non-root build. Understand before changing image. |
| 17 | `docker-compose.yml` | 5 min | 🟨 MED | Local dev stack wiring. |
| 18 | `docker-compose.swarm.yml` | 5 min | 🟨 MED | Production Swarm stack. |
| 19 | `project.config` | 3 min | 🟨 MED | Per-service single source of truth (bash KEY=value). |
| 20 | `shared-config.yaml` | 5 min | 🟨 MED | Cross-service shared values per C7. |
| 21 | `secrets.yaml.template` | 5 min | 🟨 MED | D8 secrets manifest schema. |
| 22 | `.env.example` | 2 min | 🟨 MED | Local-dev env vars. |
| 23 | `pyproject.toml` | 3 min | 🟨 MED | Python deps + tooling config. |
| 24 | `.github/workflows/per-service-ci.yml` | 5 min | ⬜ LOW | Workflow template; reference when adding a new CI job. |
| 25 | `app/__init__.py` | <1 min | ⬜ LOW | Empty package marker. |

## Total budget by priority

- HIGH only (#1-13): ~95 minutes — read top-to-bottom Day 1.
- HIGH + MED (#1-23): ~140 minutes — finish in week 1.
- Everything (#1-25): ~150 minutes — done in 2-3 sittings.

## RELATED FILES

- `ONBOARDING.md` does NOT exist in this template per F8. Use this READING-ORDER + `WHEN-YOU-GET-LOST.md` instead.
- `WALKTHROUGH.md` — the narrative-form companion to this numbered list

## Status

Scaffold. Numbers + ETAs refresh as files land (database / redis / LLM modules will add ~10 rows in later PRs).
