# yral-rishi-agent

AI chat service at agent.rishi.yral.com. Replaces yral-chat-ai.

## Architecture
Mobile app → Caddy (rishi-1/2) → This FastAPI service (rishi-4/5) → Patroni Postgres (rishi-4/5/6)
One service. One database. Code in app/.

## Code pattern
- app/config.py: _env() reads os.environ. All settings as module constants.
- app/database.py: lazy asyncpg pool. get_pool() on first call.
- app/auth.py: JWT from Authorization header. No sig verify. Issuer check.
- app/models.py: Pydantic models matching mobile Kotlin DTOs exactly.
- app/routes/: one file per feature. Router with prefix.
- app/services/: one file per external integration.
- app/repositories/: one file per table group. Raw SQL via asyncpg.

## Rules
1. SYMMETRY: every route file has the same shape. Every repo file has the same shape.
2. Mobile contract is sacred: field names, types, nullability match DTOs exactly.
3. Comments explain WHY not WHAT. No line-by-line narration.
4. Prefer English-readable names. Common coding abbreviations are fine: id, url, api, http, json, sql, utc, app, db, env, config, ws, sse, jwt, cors, crud, dto, etl, ci, cd, pr, init, auth, async, sync, repo, args, kwargs, fmt, msg, req, res, ctx, dev, prod, src, dist, deps, pkg, cmd, stdout, stderr, stdin, regex, bool, int, str, dict, list, uuid, csv, yml, yaml, toml, html, css, js, ts, py, md. See GLOSSARY.md for definitions.
5. Sentry → sentry.rishi.yral.com. Never apm.yral.com.
6. 50% faster than chat-ai on user-facing endpoints.
7. Never touch production chat-ai on rishi-1/2/3.
8. Simplicity first. If >100 lines of new code, stop and check with Rishi.
9. Before any schema change, take a pg_dump snapshot first.
10. When unsure, ask. Rishi prefers a question over undoing a mistake.

## Agent rules
- Feature branches only. Never push to main.
- One PR per concern. Under 400 lines when possible.
- Before opening PR: "Would a senior engineer say this is overcomplicated?"
- Permission deny: ssh to prod, docker service rm, rm -rf, force-push.

## Deploy process (NEVER bypass)
1. Open PR with changes.
2. Wait for CI green + Codex review.
3. Wait for Rishi explicit approval ("merge it" / "approved").
4. Merge PR to main.
5. Only THEN build image and deploy.

No exceptions for "hotfixes." A genuine hotfix is a small PR with fast review,
not a direct push. Direct deploys from unmerged branches cause source/runtime
drift and skip Codex review.

## Reading order
1. This file → 2. app/config.py → 3. app/models.py → 4. app/main.py → 5. app/routes/chat.py

## Progress tracking — PROGRESS.md vs DAILY-LOG.md

Two files, two purposes. Don't confuse them.

**PROGRESS.md** — the checklist. Tables of phases + sub-phases with status (✅ Done / ⏳ Pending / 🔄 In PR). Updated IN-PLACE when status changes. Read it when you need to know "what's done and what's left." Source of truth for current state.

**DAILY-LOG.md** — the diary. Date-stamped entries describing what you shipped each day. APPEND-ONLY — new entries go at the top. Read it when you need to know "what happened yesterday/today."

## When you ship anything, update BOTH:
1. **PROGRESS.md** — flip the relevant row(s) from ⏳ to ✅ (or 🔄 if still in PR). Update the phase total percentage and est days remaining.
2. **DAILY-LOG.md** — append a new section at the top with today's date, what you shipped, which PRs merged, what's deployed, what's pending. Keep entries skimmable — Rishi reads this in the morning to catch up on the previous day's work.

Both updates land in the same PR as the feature. If you ship a feature without updating these files, the PR is incomplete.
