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
