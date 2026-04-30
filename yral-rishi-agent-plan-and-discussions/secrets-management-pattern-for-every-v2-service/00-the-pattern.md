# Secrets Management Pattern — Every V2 Service

> **Designed for:** non-programmer + ADHD reader. The "no hunting for secrets" promise: every secret a service uses lives in ONE file (`secrets.yaml` at service root). When a deploy fails because a secret is missing, the validation script tells you EXACTLY which one + which environment.

## The pattern in plain English

```
   1. Each service has ONE file that lists every secret it needs:
      <service-folder>/secrets.yaml

   2. That file is the SOURCE OF TRUTH. Just declarations, no values.

   3. Three places hold the actual values, one per environment:
      • Local dev:    .env.local (in service folder, gitignored)
      • CI/CD:        GitHub Secrets in dolr-ai/yral-rishi-agent
      • Production:   Swarm secrets on rishi-4/5/6, sourced from
                      GitHub Secrets at deploy time
      + Team-shared:  Vault at vault.yral.com (only for things multiple
                      services need — per D1; not per-service runtime)

   4. Three scripts keep them in sync:
      • validate-secrets.sh         — checks each env has every required secret
      • sync-github-secrets.sh      — populates missing GitHub Secrets interactively
      • gen-env-example.sh          — auto-generates .env.example from secrets.yaml

   5. CI runs validate-secrets.sh on every PR — if any required secret
      is missing in any env, the PR fails before merge.
```

## Why this matters for non-programmer + ADHD

- **No hunting**: ONE file per service tells you everything.
- **No silent drift**: validation script catches missing secrets immediately.
- **Clear handoff**: when you onboard a teammate, they read `secrets.yaml` and know what they need.
- **Audit trail**: every secret has `consumed_by` (which code file reads it) and `rotation_policy`.

## Where this lives

```
yral-rishi-agent-new-service-template/
  secrets.yaml                           ← the TEMPLATE for new services
  .env.example                           ← auto-generated from secrets.yaml
  scripts/
    validate-secrets.sh                  ← runs in CI + locally
    sync-github-secrets.sh               ← interactive populator
    gen-env-example.sh                   ← regenerator

yral-rishi-agent-public-api/             ← spawned from template
  secrets.yaml                           ← service-specific, edited per service
  .env.example                           ← auto-generated
  .env.local                             ← (gitignored) local values

yral-rishi-agent-conversation-turn-orchestrator/
  secrets.yaml
  .env.example
  .env.local

... (and so on for all 13 services)

bootstrap-scripts-for-the-v2-docker-swarm-cluster/
  secrets-manifest.yaml                  ← CLUSTER-LEVEL secrets (Postgres
                                            passwords, Redis passwords, shared
                                            keys) — separate from per-service
                                            (per existing D7)
```

## Layered manifest model

We have TWO manifest layers, and they don't conflict:

| Layer | File | Contains | Owner |
|---|---|---|---|
| Cluster | `bootstrap-scripts-for-the-v2-docker-swarm-cluster/secrets-manifest.yaml` | Cluster-wide secrets (Postgres root password, Redis password, Sentinel auth, Vault token used by services to read shared secrets) | Session 1 (Infra) |
| Service | `<service>/secrets.yaml` | Per-service secrets (DATABASE_URL, GEMINI_API_KEY, SENTRY_DSN, etc.) | Each service's owning session |

Each service's `secrets.yaml` references the cluster manifest only when it consumes shared secrets (e.g., the Vault token to talk to vault.yral.com).

## The three-environment bridge

Visualized:

```
                ┌────────────────────────────────────┐
                │  secrets.yaml                       │
                │  (DECLARATION — no values)         │
                └────────────────┬───────────────────┘
                                 │
       ┌─────────────────────────┼─────────────────────────┐
       ▼                         ▼                         ▼
  ┌─────────┐             ┌─────────────┐            ┌──────────────┐
  │ LOCAL   │             │   CI/CD     │            │  PRODUCTION  │
  ├─────────┤             ├─────────────┤            ├──────────────┤
  │.env.local│            │GitHub       │            │Swarm secrets │
  │(gitignored)            │Secrets      │            │(deploy-time  │
  │          │             │             │            │ inject from  │
  │Set by:   │             │Set by:      │            │ GitHub       │
  │you, copy │             │gh secret set│            │ Secrets via  │
  │.env.example│           │via sync     │            │ deploy.yml)  │
  │and fill in│            │script       │            │              │
  │values    │             │             │            │+ Vault for   │
  │          │             │             │            │ shared (D1)  │
  └─────────┘             └─────────────┘            └──────────────┘
       │                         │                         │
       └─────────────────────────┴─────────────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ validate-secrets.sh      │
                    │ checks every env has     │
                    │ every required secret    │
                    │ from secrets.yaml        │
                    └──────────────────────────┘
```

## The everyday workflow

### When a session adds a new secret to a service

1. Edit `<service>/secrets.yaml`, add a new entry following the schema
2. Run `bash scripts/gen-env-example.sh` → regenerates `.env.example`
3. Add the value to `.env.local` (locally) and run `bash scripts/sync-github-secrets.sh` (CI)
4. Commit the updated `secrets.yaml` + `.env.example` to git
5. PR opens; CI's validate-secrets.sh confirms GitHub Secrets are populated
6. Codex review verifies the secret has clear `description` + `consumed_by`
7. Coordinator + Rishi review, merge
8. Deploy workflow inject the secret as Swarm secret on rishi-4/5/6

### When you (Rishi) need to know what secrets a service uses

```bash
cd <service-folder>
cat secrets.yaml
# OR
bash scripts/list-secrets.sh
```

Done. ONE file. No code-spelunking. ADHD-friendly.

### When deploy fails saying "missing secret X"

```bash
cd <service-folder>
bash scripts/validate-secrets.sh
```

Outputs:
```
✗ DATABASE_URL: missing in GitHub Secrets (set via: bash scripts/sync-github-secrets.sh)
✓ GEMINI_API_KEY: present in all envs
✗ SENTRY_DSN: missing in .env.local (copy .env.example to .env.local and fill)
```

Fix what's red. Re-run. Done.

## Rotation flow

When a secret needs rotating (e.g., 90-day rotation policy):

1. Generate new value (`openssl rand`, regenerate API key in vendor dashboard, etc.)
2. Update `.env.local` with new value
3. `gh secret set DATABASE_URL` — push new value to GitHub
4. Trigger redeploy (push to main, or click "Run workflow")
5. Swarm rolling deploy — new containers start with new value, old containers drain
6. Verify: nothing is broken
7. NO need to update `secrets.yaml` — it's just declarations, values changing don't change declarations

If rotating a Vault-backed shared secret: same but step 3 is `vault kv put` instead of `gh secret set`.

## What goes in Vault (shared) vs GitHub Secrets (per-service)

```
GITHUB SECRETS (per-service, primary):
  • DATABASE_URL              — service's own DB connection
  • SERVICE_REDIS_PASSWORD    — service-specific Redis ACL
  • GEMINI_API_KEY            — LLM key (could be team-shared if we promote)
  • SENTRY_DSN_<service-name> — Sentry DSN (per-project)
  • OPENROUTER_API_KEY        — service's LLM fallback
  • CLAUDE_API_KEY            — service's reasoning model
  • S3_ACCESS_KEY             — Hetzner S3 creds for backups/media
  • Custom per-service keys

VAULT (shared across services, only when MULTIPLE services need same value):
  • YRAL_METADATA_NOTIFICATION_API_KEY  — talks to metadata for push notifications
  • SHARED_LANGFUSE_PUBLIC_KEY          — all v2 services trace to one Langfuse
  • SHARED_LANGFUSE_SECRET_KEY
  • Anything else Naitik designates as team-canonical

NEVER IN EITHER:
  • Anything that's not actually a secret (use plain config files)
  • Test data or fixture values
```

Default: **GitHub Secrets first. Vault only when team-shared.**

## What you (Rishi) memorize

Just three things:

```
   1. Every service has a secrets.yaml. ONE file. Open it to see secrets.
   
   2. Three commands when a secret is wrong:
      bash scripts/validate-secrets.sh    ← what's broken
      bash scripts/sync-github-secrets.sh ← fix CI/prod
      cp .env.example .env.local && edit  ← fix local
   
   3. Never commit .env.local. Never put secret values in secrets.yaml.
      .gitignore handles both, plus pre-commit hook scans for sk-/ghp_/etc.
```

That's it. The rest is automation.
