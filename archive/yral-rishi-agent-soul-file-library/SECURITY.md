# SECURITY — yral-rishi-agent-soul-file-library

> One-line purpose: **threat model + security guarantees for this service.** What we defend against, what we don't, where the controls live in the codebase, who to call when something looks off.

## ⭐ START HERE

The 7 most load-bearing security properties of this service:

1. **Sentry never gets PII** (per H6 + A7) — the structured logger's allowlist redaction is the enforcement point.
2. **Postgres credentials never reach the image** (per D1 + D8) — Swarm secrets mounted as files in `/run/secrets/`, never as env vars in `docker-compose.swarm.yml`.
3. **JWT signature validation rolls out behind a flag** (per E9) — dual-validate during shadow phase, flipped to strict once divergence is < 0.01% for 7 days.
4. **Per-service Postgres role with schema-scoped GRANTs** (per F3) — leaked DB credential only exposes ONE service's data, not the whole cluster.
5. **No host ports exposed except 443 at the edge** (per C3) — inter-service traffic stays inside encrypted overlays.
6. **TLS terminates on rishi-1/2 Caddy** (per C10) — service-side traffic is HTTPS via Caddy's reverse-proxy, never plain HTTP on the public network.
7. **Prompt injection defense middleware pre-orchestration** (per H5) — classifier blocks extraction attempts before the LLM call.

## Authentication

Per E6 + E9. v2 services trust JWTs minted by `auth.yral.com`:

- Public-api fetches `https://auth.yral.com/.well-known/jwks.json` (host from shared-config.yaml).
- JWKS cached in Redis 1hr (per E9). Rotates when auth.yral.com rotates.
- Day-1 flag `enable_strict_jwt_signature_validation` defaults FALSE (matches current Python+Rust behavior where validation is intentionally disabled). Per E9 the dual-validate rollout flips the flag once divergence stabilizes.
- The JWT auth middleware module lands in a later PR (per the Day-2 plan).

## Authorization

- **Per-service Postgres schema isolation** per F3. Each service connects with a role that has GRANTs only on its own schema. A leaked DATABASE_URL exposes one service's data, not the cluster.
- **Per-service Redis ACL** (Session 1's stateful-core stack). Each service limited to its own key prefix.
- **Endpoint-level authorization** — to be implemented per-service. The template ships the auth middleware; per-service handlers decide what scopes they require.

## Secrets

Per D1 + D8 — declared in `secrets.yaml.template` (becomes `secrets.yaml` in a spawned service). Five inheritance secrets:

| Secret | Source per env | Blast radius |
|---|---|---|
| `DATABASE_URL` | .env.local / GitHub Secret / Swarm secret | HIGH |
| `REDIS_SENTINEL_PASSWORD` | .env.local / GitHub Secret / Swarm secret | MEDIUM |
| `SENTRY_DSN` | .env.local / GitHub Secret / Swarm secret | LOW (write-only by design) |
| `LANGFUSE_PUBLIC_KEY` | .env.local / GitHub Secret / Swarm secret | LOW |
| `LANGFUSE_SECRET_KEY` | .env.local / GitHub Secret / Swarm secret | MEDIUM |

Service-specific secrets (JWT_JWKS_URL, OPENROUTER_API_KEY, GEMINI_API_KEY, etc.) get added per-service in the spawned `secrets.yaml`.

**Pre-commit hook scan** per J5: gitleaks + custom regex blocks any commit that contains a secret-shaped string (sk-, ghp_, base64-looking 40+ chars outside .env.example, JWT format strings outside test fixtures).

## PII handling

Per H6. Enforced at two layers:

1. **Structured logger** (`app/logging.py`) — allowlist redaction processor replaces values of keys NOT on `_FIELD_ALLOWLIST` with `<redacted>`. The 22-field allowlist covers structlog built-ins, request-scoped IDs, service identity, HTTP shape, opaque user IDs, error classification, LLM telemetry.
2. **Sentry SDK** — `send_default_pii=False` in `init_sentry()`. Request bodies + headers never reach Sentry; only stack traces + structured tags.

The Langfuse client (per D4) accepts whatever callers pass. Trace-side scrubbing happens in the per-call site (the LLM client, per A10) — not yet implemented; the template just exposes `get_langfuse()`.

## Prompt injection defense

Per H5. Pre-orchestrator middleware classifies incoming user messages, blocks extraction attempts (e.g. "ignore previous instructions"), logs to Sentry with `type=prompt_injection`, returns a safe fallback.

The classifier middleware itself lives in `app/prompt_injection_defense.py` (lands in a later Day-2 PR, per the role spec).

## Network isolation

Per C3 + C10:

- TLS terminates at rishi-1/2 Caddy (Rishi-owned per A2).
- rishi-1/2 → rishi-4/5/6 hop is HTTPS over the public Hetzner network (same datacenter ~1ms).
- Inside the Swarm, three encrypted overlays per C3:
  - `yral-v2-public-web` — edge Caddy → service replicas
  - `yral-v2-internal` — service-to-service RPC
  - `yral-v2-data-plane` — services → Postgres / Redis / Langfuse
- No host ports published from the service stack (per `docker-compose.swarm.yml`).

## Out-of-scope threats (we don't defend against these today)

- **Compromised rishi-N host** — assume the host is trusted. Hardening lives at the Hetzner level + SSH key access control (per C8).
- **Compromised GitHub Action token** — limited blast radius (one service's image push to GHCR). Rotation per GitHub's own rules.
- **Side-channel attacks on the shared Patroni cluster** — schema isolation + per-service role is the only barrier. A determined attacker with one service's DATABASE_URL still can't read other services' schemas without a Postgres bug.

## Who to call when something looks off

- **Active P0 security incident** — page via Google Chat webhook (per D6).
- **Suspected secret leak** — STOP, rotate the credential immediately (per the rotation policy in secrets.yaml), then file post-mortem.
- **Codex flags a security NIT** — read it against the actual diff; known false positives exist (see CLAUDE.md "When Codex flags something").

## RELATED FILES

- `app/sentry_middleware.py` — `send_default_pii=False` enforcement
- `app/logging.py` — H6 PII allowlist redaction processor
- `app/auth.py` — JWT + JWKS (lands later)
- `app/prompt_injection_defense.py` — H5 classifier (lands later)
- `secrets.yaml.template` — D8 manifest schema + 5 inheritance secrets
- `docker-compose.swarm.yml` — overlays + Swarm secret mounts
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the threat model's underlying rules

## Day-4 threat model — C3 overlay trust + no-auth-on-Day-4 carve-out

### Trust boundary

Per C3 the cluster runs three encrypted Swarm overlays:
- `yral-v2-public-web` — edge Caddy ↔ public-api only
- `yral-v2-internal` — service ↔ service RPC (where the orchestrator → soul-file-library `GET /composed-prompt` lives)
- `yral-v2-data-plane` — every service ↔ Postgres / Redis / Langfuse

The soul-file-library binds to `yral-v2-internal`. Only Swarm-attached services reach it. Public-api never talks to soul-file-library directly; the orchestrator is the gatekeeper.

### Day-4: NO auth on `GET /composed-prompt`

Per the Day-4 directive verbatim: "Internal-only per C3 — no auth on Day 4 (overlay yral-v2-internal protects; same trust model as orchestrator → soul-file mentioned in 01-internal-rpc-contracts.md)."

**Why safe today:** the overlay is the trust boundary; orchestrator already validated the user's JWT before the internal RPC; image pulls require GHCR auth per F13.

**Why we add auth Day-5+:** defence-in-depth. The overlay is a trust boundary, not a security boundary. The future Prompt-Coach service will add an auth'd write surface; at that point the read surface gets `X-Internal-Caller: orchestrator` validation as defence-in-depth.

### Known Day-4 risks (accepted)

| Risk | Mitigation | Status |
|---|---|---|
| Compromised orchestrator → exfiltrates Soul File bodies | Overlay-only + GHCR-auth image pull + future X-Internal-Caller | Accepted Day 4 |
| Concurrent writer races overwrite history | Partial unique index + transactional retire-then-insert | Mitigation in place |
| Forgotten retired-without-replacement slot causes composer 500s | Defensive `SoulFileDataIntegrityError` raised + clear error | Mitigation in place |
| PostgreSQL connection string leaked from a service log | secrets.yaml + .env.local pattern per D1+D8; logs NEVER include the connection string | Standing template guarantee |
| Day-4 placeholder body content reaches a real user | Obviously-stubbed bracketed text + product owns the real-content PR | Accepted Day 4 |

## A1 carve-outs granted (the standing audit log)

A1 (the deletion covenant — relaxed 2026-05-14) requires every deletion to either match the 7-step safety check OR carry an explicit coordinator-approved carve-out. The carve-outs granted for THIS service are listed here so a future reader can audit every authorised deletion path without grepping git history.

| Date | Carve-out | Scope | Authoriser | Audit pointer |
|---|---|---|---|---|
| 2026-05-19 | Alembic migration `downgrade()` drops `soul_file_layers` | Reversibility of THIS migration's `upgrade()` only — table created + dropped within the same migration file. Operator action only (`alembic downgrade -1`); never automated; never on production data; CI's `test_schema_migrations.py` runs against ephemeral testcontainers-Postgres. | Rishi (Option A on the Day-4 fixup, 2026-05-19 morning). | `app/migrations/versions/001_initial_schema_and_seed.py` A1 DELETION JUSTIFICATION block; `tests/test_schema_migrations.py` A1 PROVENANCE block; PR #104 fixup commit. |

## Status

Day-4 threat model current. Day-5+ tightens to: orchestrator → soul-file-library `X-Internal-Caller` validation; Prompt-Coach auth surface; content-safety service plug-in for L3 body moderation when the data port lands (Day 4.5+).
