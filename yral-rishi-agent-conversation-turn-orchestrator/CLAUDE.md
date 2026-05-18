# CLAUDE — yral-rishi-agent-conversation-turn-orchestrator

> One-line purpose: **instructions for AI agents (Claude Code, Codex) working in this service.** What to do, what NOT to do, which constraints to cite, which patterns to follow. Read before opening any PR that touches this folder.

## ⭐ If you only read one section

**Honor A2.1.** When you're about to add >100 lines of new code, a new abstraction, a new dependency, or a multi-step workflow, STOP and ask Rishi (via the coordinator) first. Industry-standard simple > clever.

## Service identity

- **Service name placeholder:** `yral-rishi-agent-conversation-turn-orchestrator`. The new-service.sh spawner (PR 3, Day 3) replaces this everywhere when generating a real service.
- **Language:** Python 3.12 + FastAPI + asyncio + asyncpg (per F12). Don't add language alternatives.
- **Image:** GHCR (per F13). Don't change the registry.
- **Monorepo:** subfolder, not a new repo (per F16). Don't `git init` anything.

## Constraints to cite in every PR description

Top constraints AI agents bump into here:

- **A2.1** — avoid over-engineering. The first thing to cite when scope feels big.
- **A7 + C4 + D3** — Sentry = `sentry.rishi.yral.com`, NEVER `apm.yral.com`. Service tag stamped per D3.
- **B1 + B2** — every name reads as English; abbreviations only from the allowlist (`id, url, api, http, json, sql, utc, tls, dns, ssl, css, html, uuid, ip, app, init, ci, config, ...`).
- **B7** — every code file: file header + function WHAT/WHEN/WHY + role-not-syntax line comments + RELATED FILES footer.
- **C7** — shared values live in `shared-config.yaml`. No hardcoded shared values in code.
- **D8** — every secret declared in `secrets.yaml` (or `secrets.yaml.template` for the template) with full schema.
- **F8** — every service has 8 required docs: DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY / WALKTHROUGH / GLOSSARY / WHEN-YOU-GET-LOST. **DO NOT INVENT OTHER NAMES** (this list is locked per CONSTRAINTS F8 + Rishi's 2026-04-27 doc-set decision).
- **F12** — Python 3.12 stack uniformly.
- **F16** — monorepo. Path-scoped CI per service.
- **H6** — PII allowlist enforcement in the log processor.
- **I6** — push back ONCE on industry-non-standard / likely-wrong decisions, then accept Rishi's call. Don't silently agree when you spot a concern.

## When you're asked to add a new module to `app/`

1. Check `READING-ORDER.md` — is the new module's purpose already covered by an existing one?
2. Match the existing module shape: file header (⭐ START HERE), function WHAT/WHEN/WHY, role-not-syntax comments, RELATED FILES footer.
3. If the module needs init at module-load, follow the `init_*` pattern (per the B2 carve-out PR #26 — `init_sentry`, `init_langfuse`, `configure_logging` are precedents).
4. Wire into `app/main.py` AFTER existing inits but BEFORE app creation (if it's a module-load init) OR via `app.add_middleware(...)` (if it's a Starlette middleware — note that `add_middleware` is LIFO so order matters).
5. Add deps to `pyproject.toml` with a one-line comment explaining what each gives us.

## When you're asked to modify `app/main.py`

Be EXTRA careful with the import + init order at the top of the file:

```
init_sentry()         # MUST come before FastAPI app exists (hooks into Starlette)
init_langfuse()       # uniform pattern; safe to reorder relative to Sentry
configure_logging()   # before app so startup logs are structured
app = FastAPI(...)
app.add_middleware(RequestIdMiddleware)   # LIFO — added LAST runs OUTERMOST
```

If you add new middleware, they go BEFORE the `add_middleware(RequestIdMiddleware)` line so request-ID assignment is the outermost layer.

## When you're writing tests

Per J1's risk-weighted coverage tiers. The template inherits the J1 HOT/WARM/COOL classifications by the spawned service's name. Tests follow B7 style (plain-English names, WHAT/WHEN/WHY docstrings, file headers, priority order, role-not-syntax comments).

## When you're writing CI

You probably aren't — `.github/workflows/` at the repo root is coordinator-only per I9. If the task seems to require root workflow changes, STOP and surface to the coordinator. The workflow TEMPLATE inside this folder (`.github/workflows/per-service-ci.yml`) IS in scope; it's the source of truth that new-service.sh copies to root at spawn time.

## When Codex flags something

Read the Codex comment against the ACTUAL diff before acting. Known false-positive patterns observed:
- Hallucinated phrasing changes (e.g. flagging `per-request` as if it said `per-req`).
- Hallucinated BLOCKER on names that are already on the B2 allowlist.

If Codex disagrees with you and the diff supports your version, push back via PR comment with the actual diff quote. Don't silently change correct code.

## Cross-check coordinator's constraint citations

When a coordinator message cites a CONSTRAINTS row (e.g. "per F8 the docs are X / Y / Z"), open `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` and confirm the row text matches. Catching coordinator drift mid-flight saves a redo cycle. (See PR #32 closed for an example where this would have helped — coordinator gave wrong doc names, I shipped them, Codex caught it, redo cost a full PR cycle.)

## RELATED FILES

- `DEEP-DIVE.md` — visual mental model
- `READING-ORDER.md` — what to read in what order
- `WALKTHROUGH.md` — narrative request trace
- `GLOSSARY.md` — domain terms
- `WHEN-YOU-GET-LOST.md` — north-star recovery
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the source of truth

## Status

Scaffold. Real per-task instructions fill in as the template grows + spawns the first real service.
