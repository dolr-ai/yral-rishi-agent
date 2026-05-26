# `secrets.yaml` — schema + worked example

## Schema (every entry has these fields)

```yaml
service: <service-name>          # e.g. yral-rishi-agent-public-api
                                  # MUST match folder name; used by bridge scripts

secrets:
  - name: SECRET_NAME             # UPPER_SNAKE_CASE per B1
    description: |
      One-sentence explanation of what this secret IS.
      Plain English. Non-programmer must understand it from this line alone.

    required_in:                   # which environments REQUIRE this to be set
      - local                      # local dev (.env.local)
      - ci                         # GitHub Secrets in CI workflows
      - production                 # Swarm secret on rishi-4/5/6

    source:                        # WHERE the value comes from per environment
      local: ".env.local"
      ci: "GitHub Secret <SECRET_NAME>"
      production: "GitHub Secret <SECRET_NAME> → Swarm secret at deploy"
                                   # OR for shared:
                                   # production: "Vault path secret/yral-v2/shared/<key>"

    rotation_policy: |
      Plain-English rotation cadence.
      e.g. "every 90 days" or "stable, only changes on auth.yral.com migration"
      or "rotated when team member leaves with access"

    consumed_by:                   # which CODE FILES read this secret
      - app/database.py            # so when the secret rotates, you know what tests
      - app/migrations/runner.py   # to run

    classification:                 # for risk awareness
      blast_radius: low|medium|high   # if leaked, how bad?
      access_pattern: read|write|admin  # what can someone do with it?

    notes: |                       # OPTIONAL — anything else worth knowing
      e.g. "Generated via openssl rand -base64 32"
      e.g. "Owner: Naitik (yral-billing); rotate via vault.yral.com UI"
```

## Worked example — `yral-rishi-agent-public-api/secrets.yaml`

```yaml
service: yral-rishi-agent-public-api

secrets:
  - name: DATABASE_URL
    description: |
      Postgres connection string for the public-api service's own schema.
      Format: postgresql://user:password@pgbouncer:5432/yral_v2?options=-csearch_path=public_api
    required_in: [local, ci, production]
    source:
      local: ".env.local (point at local docker-compose Postgres)"
      ci: "GitHub Secret DATABASE_URL_PUBLIC_API"
      production: "GitHub Secret DATABASE_URL_PUBLIC_API → Swarm secret at deploy"
    rotation_policy: "every 90 days; coordinated with Patroni rotation script"
    consumed_by:
      - app/database.py
      - alembic/env.py
    classification:
      blast_radius: high
      access_pattern: read+write
    notes: |
      Service uses its own schema (public_api.*) within the shared Patroni cluster.
      Connection goes through pgBouncer on rishi-4 (managed by Session 1).

  - name: JWT_JWKS_URL
    description: |
      URL to fetch the JSON Web Key Set used to validate JWTs from yral-auth-v2.
      Per E9 — used by the JWKS-based RS256 signature validation rolling out in
      shadow mode first.
    required_in: [local, ci, production]
    source:
      local: ".env.local"
      ci: "GitHub Secret JWT_JWKS_URL"
      production: "GitHub Secret JWT_JWKS_URL → Swarm secret"
    rotation_policy: "stable; only changes if auth.yral.com migrates domain"
    consumed_by:
      - app/auth.py
    classification:
      blast_radius: low
      access_pattern: read
    notes: |
      Default value: https://auth.yral.com/.well-known/jwks.json
      Cached in Redis 1 hour TTL per E9.

  - name: SENTRY_DSN_PUBLIC_API
    description: |
      Sentry DSN for THIS service's project at sentry.rishi.yral.com (NOT
      apm.yral.com per A7). Tag: service=yral-rishi-agent-public-api per D3.
    required_in: [local, ci, production]
    source:
      local: ".env.local (use a local sentry-dev project to avoid noise in prod)"
      ci: "GitHub Secret SENTRY_DSN_PUBLIC_API"
      production: "GitHub Secret SENTRY_DSN_PUBLIC_API → Swarm secret"
    rotation_policy: "stable; rotates only on Sentry org migration"
    consumed_by:
      - app/sentry_middleware.py
    classification:
      blast_radius: low
      access_pattern: write-only (DSN can only post events)
    notes: |
      Set environment=local|staging|production via SENTRY_ENVIRONMENT separately.

  - name: REDIS_SENTINEL_PASSWORD
    description: |
      Auth password for Redis Sentinel cluster (per C11) — primary on rishi-4,
      replica on rishi-5, sentinels across rishi-4/5/6.
    required_in: [local, ci, production]
    source:
      local: ".env.local (local docker-compose Redis can use empty or 'devpassword')"
      ci: "GitHub Secret REDIS_SENTINEL_PASSWORD"
      production: "GitHub Secret REDIS_SENTINEL_PASSWORD → Swarm secret"
    rotation_policy: "every 180 days"
    consumed_by:
      - app/redis_client.py
    classification:
      blast_radius: medium
      access_pattern: read+write (per-service Redis ACL limits keys)

  - name: OPENROUTER_API_KEY
    description: |
      OpenRouter API key for Tara's per-influencer routing (per A10) and
      is_nsfw-flagged routes. Falls through to Gemini for default archetype.
    required_in: [local, ci, production]
    source:
      local: ".env.local (use a personal dev key, low spend cap)"
      ci: "GitHub Secret OPENROUTER_API_KEY"
      production: "GitHub Secret OPENROUTER_API_KEY → Swarm secret"
    rotation_policy: "every 90 days; check OpenRouter dashboard for unauthorized usage"
    consumed_by:
      - app/llm_client.py
    classification:
      blast_radius: high
      access_pattern: read+spend
    notes: |
      Watch the OpenRouter dashboard for spend anomalies per E4 runaway-protection
      pattern (high cap, not budget gate).

  - name: GEMINI_API_KEY
    description: |
      Gemini API key for default archetype routing (per A10). The most-used
      LLM provider in v2; rate limits matter for capacity planning.
    required_in: [local, ci, production]
    source:
      local: ".env.local"
      ci: "GitHub Secret GEMINI_API_KEY"
      production: "GitHub Secret GEMINI_API_KEY → Swarm secret"
    rotation_policy: "every 90 days"
    consumed_by:
      - app/llm_client.py
    classification:
      blast_radius: high
      access_pattern: read+spend

  - name: YRAL_METADATA_NOTIFICATION_API_KEY
    description: |
      SHARED key for talking to yral-metadata service for push notifications.
      Per D1 — this is the team-shared secret pattern; lives in Vault, NOT
      GitHub Secrets per-service.
    required_in: [local, ci, production]
    source:
      local: ".env.local (use a dev key obtained from Naitik)"
      ci: "Vault path secret/team-shared/yral-metadata-notification-key (CI workflow
           reads via VAULT_TOKEN)"
      production: "Vault read at runtime via infra.get_secret('team-shared/yral-metadata-notification-key')"
    rotation_policy: "Naitik decides; we read whatever Vault has"
    consumed_by:
      - app/notifications.py
    classification:
      blast_radius: medium
      access_pattern: write (sends push notifications)
    notes: |
      The ONLY secret in this file that uses Vault. Everything else uses GitHub
      Secrets per D1.
```

## What `secrets.yaml` does NOT contain

- Actual values (those live in `.env.local`, GitHub Secrets, or Vault)
- Test fixtures or non-secret config (use `project.config` or `shared-config.yaml`)
- Internal-only values like feature flags (use `app/feature_flags.py` per F11)

## Validation rule baked into CI

CI's `validate-secrets.sh` checks:

1. Every entry in `secrets.yaml` has all required fields filled in
2. Names follow `UPPER_SNAKE_CASE` per B1
3. Names not in the banned-abbreviations block-list per B2
4. `consumed_by` files actually exist in the repo (so secrets aren't orphaned)
5. For `production` env: corresponding GitHub Secret EXISTS (via `gh secret list`)
6. For shared secrets: corresponding Vault path EXISTS (via `vault kv get -dryrun`)
7. `.env.example` matches the secrets.yaml declarations (no drift)

If ANY check fails, PR is blocked. Per I9 + I16.

## Why this much structure for non-programmer + ADHD

- **One file = no hunting** for what secrets a service needs
- **Plain English `description`** so you understand what each secret does without reading code
- **`consumed_by`** so when rotating, you know what tests to run
- **`rotation_policy`** so 90-day-late rotations don't silently happen
- **`classification.blast_radius`** so you know which secrets to guard hardest
- **CI validation** so the human (you) never has to manually check sync between .env.local / GitHub Secrets / Vault
