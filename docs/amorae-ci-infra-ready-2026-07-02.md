# amorae-web — CI/infra ready for Session 6 verification (2026-07-02)

**From:** amorae.ai Web Session · **To:** Session 6
Repo: `~/Claude Projects/amorae-web`, branch `feat/amorae-walking-skeleton`
(local only — no GitHub remote yet, pending Rishi Q1/Q2 below).

## What's drafted + locally verified
- **Walking skeleton** (commit `aa546f0`) — landing → 18+ gate → SSE text
  chat, persisted to `amorae_db`. Verified end-to-end via isolated Docker.
- **CI/CD + Swarm** (commit `47af1c4`), mirroring yral-rishi-agent:
  - `ci.yml` (ruff + pytest + build-push), `security.yml` (gitleaks +
    pip-audit + trivy), `codex-review.yml` **with the `ready_for_review`
    fix** for the draft-PR gap you hit 2026-06-26.
  - `deploy.yml` (auto-deploy after CI, /health probe, auto-rollback,
    `:stable` tag) + `rollback.yml`. **No auto-migration step** —
    `amorae_db` migrations stay manual (Rule 9) until a runner is ported.
  - `docker-compose.swarm.yml` (2 replicas, `yral-v2-data-plane`, port 8003).
  - ruff clean, `ruff format --check` clean, 6/6 pytest pass, all 7 YAML +
    codex script parse OK (in `python:3.12-slim`, matching CI).

## Need Rishi (blocks remote creation)
- **Q1 — GitHub org:** `dolr-ai` (same as v2) or a NEW separate adult-brand
  org? Everything currently hardcodes `ghcr.io/dolr-ai/amorae-web`; a
  separate org = find-replace of `dolr-ai` across the 5 workflow/compose files.
- **Q2 — Repo name:** `amorae-web` (my recommendation — off the yral-rishi-*
  prefix, matches the separate brand). Confirm.

## Swarm stack — 4 items for you to verify before it runs against prod
(All flagged in a ⚠️ block at the top of `docker-compose.swarm.yml`.)
1. **Service name** — `deploy.yml` targets bare `amorae-web` via
   `docker service update`. Confirm how v2's `yral-rishi-agent` service is
   actually named/managed (standalone service vs stack-prefixed) and match it.
2. **Overlay networks** — is `yral-v2-data-plane` the right + sufficient
   external overlay for BOTH L2-Caddy reach and `amorae_db` access, or is a
   public-web/internal overlay also needed?
3. **Placement** — pin 2 replicas to rishi-4/5 (node.hostname/label) once
   real hostnames/labels are confirmed.
4. **Caddy** — does L2 routing use the `caddy.*` labels (Swarm plugin) or
   the edge `amorae-rishi-yral.caddy` on rishi-1/2/3, or both?

## Also for you to route
- `docs/amorae-v2-contract-2026-07-01.md` → dev session, so the v2 side
  (handoff/exchange, consent audit, context-read) matches shape. If dev
  pushes back on `/spicy/context` (§3), I conform.
