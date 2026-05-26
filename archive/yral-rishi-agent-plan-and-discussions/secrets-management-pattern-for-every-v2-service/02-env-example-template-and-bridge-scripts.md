# `.env.example` template + the three bridge scripts

## Auto-generated `.env.example`

Generated from `secrets.yaml` by `scripts/gen-env-example.sh`. Looks like this:

```bash
# .env.example for yral-rishi-agent-public-api
# AUTO-GENERATED from secrets.yaml — DO NOT EDIT BY HAND.
# Run `bash scripts/gen-env-example.sh` to regenerate.
#
# To use locally:
#   cp .env.example .env.local
#   Then fill in REAL VALUES in .env.local (which is gitignored)

# DATABASE_URL — Postgres connection string for the public-api service's own
# schema. Format: postgresql://user:password@pgbouncer:5432/yral_v2?...
# Source: .env.local | GitHub Secret DATABASE_URL_PUBLIC_API | Swarm secret
DATABASE_URL=

# JWT_JWKS_URL — URL to fetch the JSON Web Key Set used to validate JWTs from
# yral-auth-v2. Default: https://auth.yral.com/.well-known/jwks.json
# Source: .env.local | GitHub Secret JWT_JWKS_URL | Swarm secret
JWT_JWKS_URL=

# SENTRY_DSN_PUBLIC_API — Sentry DSN for THIS service's project at
# sentry.rishi.yral.com (NOT apm.yral.com per A7).
# Source: .env.local | GitHub Secret SENTRY_DSN_PUBLIC_API | Swarm secret
SENTRY_DSN_PUBLIC_API=

# REDIS_SENTINEL_PASSWORD — Auth password for Redis Sentinel cluster (per C11).
# Source: .env.local | GitHub Secret REDIS_SENTINEL_PASSWORD | Swarm secret
REDIS_SENTINEL_PASSWORD=

# OPENROUTER_API_KEY — OpenRouter API key for Tara's per-influencer routing
# (per A10) and is_nsfw-flagged routes.
# Source: .env.local | GitHub Secret OPENROUTER_API_KEY | Swarm secret
OPENROUTER_API_KEY=

# GEMINI_API_KEY — Gemini API key for default archetype routing (per A10).
# Source: .env.local | GitHub Secret GEMINI_API_KEY | Swarm secret
GEMINI_API_KEY=

# YRAL_METADATA_NOTIFICATION_API_KEY — SHARED key for yral-metadata push
# notifications. Per D1 — Vault-backed, not per-service GitHub Secret.
# Source: .env.local (dev key) | CI: Vault via VAULT_TOKEN | Production: Vault
YRAL_METADATA_NOTIFICATION_API_KEY=
```

Each variable has 3 lines: name, description, source-per-env. Auto-comment-format makes it skim-friendly.

## `.env.local` (the file YOU edit, never committed)

Same structure as `.env.example` but with REAL values:

```bash
# .env.local — local dev values, DO NOT COMMIT
DATABASE_URL=postgresql://devuser:devpass@localhost:5432/yral_v2_dev?...
JWT_JWKS_URL=https://auth.yral.com/.well-known/jwks.json
SENTRY_DSN_PUBLIC_API=https://abc123@sentry.rishi.yral.com/2
REDIS_SENTINEL_PASSWORD=devpassword
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=AIza...
YRAL_METADATA_NOTIFICATION_API_KEY=ask_naitik_for_dev_key
```

Always gitignored. Pre-commit hook scans for accidental commits of secret-shape strings.

---

## Bridge script #1 — `validate-secrets.sh`

Lives at: `scripts/validate-secrets.sh` in EACH service folder (also in template).

What it does:
1. Reads `secrets.yaml`
2. For each entry, checks the source declared per environment
3. Reports what's missing where

Pseudo-code (real version is bash + yq + gh CLI):

```bash
#!/usr/bin/env bash
# WHAT — Validates that every secret declared in secrets.yaml has a value
#        in every environment (local, ci, production) where it's required.
# WHEN — Runs in CI on every PR. Run locally before opening a PR. Run
#        when "missing secret" deploy errors happen.
# WHY  — Prevents the "I deployed and it crashed because X wasn't set" pain.

set -e

SERVICE_DIR=$(pwd)
SECRETS_YAML="$SERVICE_DIR/secrets.yaml"

if [ ! -f "$SECRETS_YAML" ]; then
  echo "✗ No secrets.yaml found in $SERVICE_DIR"
  exit 1
fi

echo "Validating secrets for service at $SERVICE_DIR..."
echo ""

EXIT_CODE=0

# Loop through each declared secret
for secret_name in $(yq '.secrets[].name' "$SECRETS_YAML"); do
  required_in=$(yq ".secrets[] | select(.name == \"$secret_name\") | .required_in[]" "$SECRETS_YAML")

  # Check local
  if echo "$required_in" | grep -q "local"; then
    if [ -f "$SERVICE_DIR/.env.local" ] && grep -q "^$secret_name=" "$SERVICE_DIR/.env.local"; then
      echo "✓ $secret_name: present in .env.local"
    else
      echo "✗ $secret_name: MISSING in .env.local (run: cp .env.example .env.local then fill in)"
      EXIT_CODE=1
    fi
  fi

  # Check CI / GitHub Secrets
  if echo "$required_in" | grep -q "ci\|production"; then
    if gh secret list --repo dolr-ai/yral-rishi-agent | grep -q "^$secret_name"; then
      echo "✓ $secret_name: present in GitHub Secrets"
    else
      echo "✗ $secret_name: MISSING in GitHub Secrets (run: bash scripts/sync-github-secrets.sh)"
      EXIT_CODE=1
    fi
  fi

  # Check production / Vault (only for shared secrets)
  source_prod=$(yq ".secrets[] | select(.name == \"$secret_name\") | .source.production" "$SECRETS_YAML")
  if echo "$source_prod" | grep -q "Vault path"; then
    vault_path=$(echo "$source_prod" | grep -oP 'secret/[^\s]+')
    if vault kv get "$vault_path" >/dev/null 2>&1; then
      echo "✓ $secret_name: present in Vault at $vault_path"
    else
      echo "✗ $secret_name: MISSING in Vault at $vault_path (ask Naitik)"
      EXIT_CODE=1
    fi
  fi
done

echo ""
if [ $EXIT_CODE -eq 0 ]; then
  echo "✅ All secrets validated."
else
  echo "❌ Some secrets are missing. Fix above, then re-run."
fi

exit $EXIT_CODE
```

Output looks like:

```
Validating secrets for service at /Users/.../yral-rishi-agent-public-api...

✓ DATABASE_URL: present in .env.local
✓ DATABASE_URL: present in GitHub Secrets
✗ JWT_JWKS_URL: MISSING in .env.local (run: cp .env.example .env.local then fill in)
✓ JWT_JWKS_URL: present in GitHub Secrets
✓ SENTRY_DSN_PUBLIC_API: present in .env.local
✓ SENTRY_DSN_PUBLIC_API: present in GitHub Secrets
✗ YRAL_METADATA_NOTIFICATION_API_KEY: MISSING in Vault at secret/team-shared/yral-metadata-notification-key (ask Naitik)

❌ Some secrets are missing. Fix above, then re-run.
```

ADHD-friendly: red ✗ tells you exactly what's broken AND what to do about it.

---

## Bridge script #2 — `sync-github-secrets.sh`

What it does:
1. Reads `secrets.yaml`
2. For each secret declared as `required_in: [..., ci, ...]`, checks if it's set in GitHub Secrets
3. If missing, prompts you interactively to enter the value
4. Calls `gh secret set` to populate

Pseudo-code:

```bash
#!/usr/bin/env bash
# WHAT — For each secret in secrets.yaml that's missing in GitHub Secrets,
#        prompt for the value and set it via `gh secret set`.
# WHEN — When validate-secrets.sh says GitHub Secrets are missing.
# WHY  — Don't leave you hunting through the GitHub web UI; do it from
#        terminal in 30 seconds per secret.

set -e

SECRETS_YAML="$(pwd)/secrets.yaml"
REPO="dolr-ai/yral-rishi-agent"

for secret_name in $(yq '.secrets[].name' "$SECRETS_YAML"); do
  required_in=$(yq ".secrets[] | select(.name == \"$secret_name\") | .required_in[]" "$SECRETS_YAML")

  if echo "$required_in" | grep -q "ci\|production"; then
    if gh secret list --repo "$REPO" | grep -q "^$secret_name"; then
      echo "✓ $secret_name already set"
    else
      description=$(yq ".secrets[] | select(.name == \"$secret_name\") | .description" "$SECRETS_YAML")
      echo ""
      echo "──────────────────────────────────────────────────"
      echo "Need value for: $secret_name"
      echo "Description: $description"
      echo ""
      echo "Paste the value (will not echo to screen):"
      read -s value
      echo "$value" | gh secret set "$secret_name" --repo "$REPO" --body -
      echo "✓ $secret_name set in GitHub Secrets"
    fi
  fi
done

echo ""
echo "✅ GitHub Secrets sync complete. Re-run validate-secrets.sh to confirm."
```

---

## Bridge script #3 — `gen-env-example.sh`

What it does:
1. Reads `secrets.yaml`
2. Generates `.env.example` with one block per secret (name + description + source comment + empty value)
3. Overwrites the existing `.env.example`

Pseudo-code:

```bash
#!/usr/bin/env bash
# WHAT — Regenerate .env.example from secrets.yaml so they never drift.
# WHEN — After every edit to secrets.yaml. CI also runs this and fails
#        if .env.example has drifted.
# WHY  — A stale .env.example is the source of half of all "I followed
#        the README and it didn't work" issues.

set -e

SECRETS_YAML="$(pwd)/secrets.yaml"
ENV_EXAMPLE="$(pwd)/.env.example"
SERVICE_NAME=$(yq '.service' "$SECRETS_YAML")

cat > "$ENV_EXAMPLE" <<HEADER
# .env.example for $SERVICE_NAME
# AUTO-GENERATED from secrets.yaml — DO NOT EDIT BY HAND.
# Run \`bash scripts/gen-env-example.sh\` to regenerate.
#
# To use locally:
#   cp .env.example .env.local
#   Then fill in REAL VALUES in .env.local (which is gitignored)

HEADER

for secret_name in $(yq '.secrets[].name' "$SECRETS_YAML"); do
  description=$(yq ".secrets[] | select(.name == \"$secret_name\") | .description" "$SECRETS_YAML" | sed 's/^/# /' | head -3)
  source_local=$(yq ".secrets[] | select(.name == \"$secret_name\") | .source.local" "$SECRETS_YAML")
  source_ci=$(yq ".secrets[] | select(.name == \"$secret_name\") | .source.ci" "$SECRETS_YAML")
  source_prod=$(yq ".secrets[] | select(.name == \"$secret_name\") | .source.production" "$SECRETS_YAML")

  cat >> "$ENV_EXAMPLE" <<ENTRY

# $secret_name — $(yq ".secrets[] | select(.name == \"$secret_name\") | .description" "$SECRETS_YAML" | head -1)
# Source: $source_local | $source_ci | $source_prod
$secret_name=
ENTRY
done

echo "✅ .env.example regenerated. Diff with previous version:"
git diff "$ENV_EXAMPLE"
```

---

## CI workflow — `.github/workflows/lint-secrets-hygiene.yml`

```yaml
# WHAT — Validates that .env.example matches secrets.yaml AND that all
#        required GitHub Secrets are populated.
# WHEN — Every PR opened against main.
# WHY  — Prevents "I added a secret in code but forgot the manifest" drift.

name: Lint Secrets Hygiene

on:
  pull_request:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install yq + gh
        run: |
          # yq for parsing YAML; gh for checking secret list
          sudo snap install yq
          # gh is pre-installed on Ubuntu runners

      - name: Find services with secrets.yaml
        id: find_services
        run: |
          # Look for any folder with a secrets.yaml at its root
          mapfile -t SERVICES < <(find . -maxdepth 2 -name "secrets.yaml" -exec dirname {} \;)
          echo "services=${SERVICES[*]}" >> $GITHUB_OUTPUT

      - name: Validate every service's secrets sync
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          for service in ${{ steps.find_services.outputs.services }}; do
            echo "=== Validating $service ==="
            cd "$service"
            bash scripts/validate-secrets.sh
            echo ""
            # Check .env.example is in sync with secrets.yaml
            bash scripts/gen-env-example.sh > /tmp/expected-env-example
            diff .env.example /tmp/expected-env-example || {
              echo "✗ .env.example is stale. Run gen-env-example.sh and commit."
              exit 1
            }
            cd - >/dev/null
          done
```

---

## How this plugs into the new-service.sh spawner

When Session 2 spawns a new service via the template's `new-service.sh`:

```bash
bash scripts/new-service.sh --name yral-rishi-agent-conversation-turn-orchestrator
```

The spawner does:
1. Creates folder `yral-rishi-agent-conversation-turn-orchestrator/`
2. Copies template files including `secrets.yaml.template`
3. Renames it to `secrets.yaml`
4. Replaces `<service-name>` placeholders with actual service name
5. Creates a stub set of secrets (DATABASE_URL, REDIS_SENTINEL_PASSWORD, SENTRY_DSN_<service>, GEMINI_API_KEY, JWT_JWKS_URL — common ones)
6. Runs `gen-env-example.sh` to create initial `.env.example`
7. Creates empty `.env.local` — gitignored
8. Adds `secrets.yaml` to the Tier-2 doc (function/class headers reference it)
9. Service is ready; coordinator + session-owner customize secrets as needed

---

## Bookkeeping summary

```
   Files created PER SERVICE:
   ───────────────────────────
   secrets.yaml         ← committed; declarations only; source of truth
   .env.example         ← committed; auto-generated from secrets.yaml
   .env.local           ← gitignored; local values; YOU edit
   scripts/validate-secrets.sh    ← committed; bridge script (template-spawned)
   scripts/sync-github-secrets.sh ← committed; bridge script
   scripts/gen-env-example.sh     ← committed; bridge script

   Files updated GLOBALLY:
   ────────────────────────
   .gitignore                                     ← .env.local pattern
   .github/workflows/lint-secrets-hygiene.yml     ← CI enforcement
   yral-rishi-agent-new-service-template/         ← template scaffolding for above

   What's NOT in this system:
   ───────────────────────────
   No proprietary secret manager
   No config server
   No new external dependencies (just gh CLI + yq + Vault we already have)
```
