# SECURITY — yral-rishi-agent-public-api

> One-line purpose: **threat model + security guarantees for this service.** What we defend against, what we don't, where the controls live in the codebase, who to call when something looks off.

## ⭐ START HERE

The 7 most load-bearing security properties of this service:

1. **Sentry never gets PII** (per H6 + A7) — the structured logger's allowlist redaction is the enforcement point.
2. **Service credentials never reach the image** (per D1 + D8) — Swarm secrets mounted as files in `/run/secrets/`, never as env vars in `docker-compose.swarm.yml`. Phase-1 secret surface: `REDIS_URL` + `SENTRY_DSN` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (no Postgres credential — public-api is a thin HTTP gateway with no DB consumer).
3. **JWT signature validation rolls out behind a flag** (per E9) — dual-validate during shadow phase, flipped to strict once divergence is < 0.01% for 7 days.
4. **Per-service Redis key-prefix ACL** (Session 1's stateful-core stack) — leaked `REDIS_URL` only exposes this service's cache contents (JWKS + idempotency), not other services'.
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

- **Per-service Redis ACL** (Session 1's stateful-core stack). Each service limited to its own key prefix; a leaked `REDIS_URL` only exposes this service's cache contents (JWKS bytes + idempotency keys), not other services'.
- **No direct Postgres access from this service** — Phase-1 public-api is a thin HTTP gateway. Conversation state lives behind the orchestrator (per F3 each owning-service has its own schema isolation); public-api never sees `DATABASE_URL`.
- **Endpoint-level authorization** — the JWT shadow rig (Day 3-4B) gates every chat + influencer handler via `require_authenticated_user`; health endpoints stay auth-free per F9.

## Secrets

Per D1 + D8 — declared in `secrets.yaml`. Phase-1 set (trimmed from the template's 5-secret inheritance via a runtime-import audit, 2026-05-20 Day-5):

| Secret | Source per env | Blast radius |
|---|---|---|
| `REDIS_URL` | .env.local / GitHub Secret / Swarm secret | MEDIUM |
| `SENTRY_DSN` | .env.local / GitHub Secret / Swarm secret | LOW (write-only by design) |
| `LANGFUSE_PUBLIC_KEY` | .env.local / GitHub Secret / Swarm secret | LOW |
| `LANGFUSE_SECRET_KEY` | .env.local / GitHub Secret / Swarm secret | MEDIUM |

The template's inherited `DATABASE_URL` is not in this list because Phase-1 public-api has no direct Postgres consumer. The template's `REDIS_SENTINEL_PASSWORD` was renamed to `REDIS_URL` because `app/redis_client.py` uses `redis.Redis.from_url(...)` (auth embedded in URL), not a separate password env var.

Service-specific secrets (OPENROUTER_API_KEY for Tara routing, etc.) get added below the Phase-1 set as those features land.

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
- **Side-channel attacks on the shared Patroni cluster** — Phase-1 public-api doesn't reach Postgres directly, so this surface is N/A here. Services that DO own a schema (orchestrator, soul-file-library, etc.) rely on schema isolation + per-service role per F3.

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

## Status

Scaffold. Real per-threat detail (specific attack scenarios, mitigation depth, incident-response chains) fills in Days 5-6 once auth + LLM modules + content-safety service exist.
