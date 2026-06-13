# Secret rotation runbook (Phase 24.4 / 21αβ.H8)

**Phase**: 21αβ.H8 / Phase 24.4
**Status**: established 2026-06-13
**Audience**: Rishi (operator), session 6 (orchestrator)

## Why this runbook exists

Every secret in production has a rotation path. Without a documented runbook, "rotate after a leak" turns into a panicked 2am session where we discover the JWT signing key is on someone's laptop or the Hetzner S3 keys haven't been touched in 18 months. This doc closes that gap.

**Cadence**: rotate every secret at least annually. Rotate immediately on suspected compromise (leak, departing team member, accidental commit to a public repo).

## How to use this runbook

Each section follows the same shape:
1. **What** — what the secret unlocks
2. **Where stored** — Keychain / Swarm secret / GitHub Secret / env var (production side and operator side)
3. **Rotate** — exact commands
4. **Verify** — how to confirm the rotation worked end-to-end
5. **Who to notify** — internal + external dependencies

If a secret you need to rotate isn't in this doc: add a section + open a PR. Don't rotate "from memory."

---

## Conventions

### macOS Keychain (operator-side)

All v2-build secrets on Rishi's Mac use `account=dolr-ai`. Service names follow `<service-shorthand>` (no `yral-` prefix). See `feedback_keychain_service_names.md` in session memory for the full convention.

Source a secret:

```sh
security find-generic-password -a dolr-ai -s <service-name> -w 2>/dev/null
```

Store a new secret (or update an existing):

```sh
security add-generic-password -a dolr-ai -s <service-name> -w "<new-value>" -U
```

The `-U` flag updates if the entry already exists.

### Docker Swarm secrets (production-side)

Production secrets are mounted as files into the `yral-rishi-agent` service via Swarm secrets:

```sh
# List
ssh rishi-deploy@<manager> "docker secret ls | grep yral"

# Inspect (metadata only; values are encrypted)
ssh rishi-deploy@<manager> "docker secret inspect <secret-name>"
```

Pattern for rotating a Swarm secret WITHOUT downtime:

1. Create a new secret with a SHA8 suffix: `<name>_<sha8>`
2. Update the service to use the new secret + remove the old one in a single `service update`
3. Tasks roll one at a time → zero downtime
4. After the service is healthy, `docker secret rm <old-name>`

Example (see `.github/workflows/rotate-runpod-vllm-key.yml` for the canonical pattern):

```sh
NEW_SHA="$(echo "$NEW_VALUE" | sha256sum | cut -c1-8)"
NEW_NAME="RUNPOD_VLLM_API_KEY_${NEW_SHA}"
echo "$NEW_VALUE" | docker secret create "${NEW_NAME}" -
docker service update \
    --secret-rm RUNPOD_VLLM_API_KEY \
    --secret-add "source=${NEW_NAME},target=RUNPOD_VLLM_API_KEY" \
    yral-rishi-agent
docker secret rm RUNPOD_VLLM_API_KEY  # only after service is healthy
```

### GitHub Secrets (CI / workflow-side)

Repo-level secrets at https://github.com/dolr-ai/yral-rishi-agent/settings/secrets/actions. Updates take effect on the next workflow run; no roll required.

### Sentry / logs hygiene

When rotating: **NEVER paste the new value into Slack, GitHub PR descriptions, or Sentry comments**. Sentry has full-text retention for 90 days. Use length-checks (`${#NEW_VALUE}`) to confirm presence without leaking the value.

---

## Inventory by secret

### 1. `GEMINI_API_KEY`

**What**: Google AI Studio bearer token for `generativelanguage.googleapis.com`. Powers the primary chat LLM + Coach + skills.

**Where stored**:
- Operator: Google Cloud Console → Vertex / AI Studio
- Production: Swarm secret `GEMINI_API_KEY` mounted at `/run/secrets/GEMINI_API_KEY`
- App reads: `app/config.py:GEMINI_API_KEY` via `_env()` (falls back to env var if file missing)

**Rotate**:

1. AI Studio → APIs & Services → Credentials → API Keys → **Create new** (separate from existing — don't disable the old one yet)
2. Copy new key to Keychain:
   ```sh
   security add-generic-password -a dolr-ai -s gemini-api-key -w "<new-value>" -U
   ```
3. Push to production:
   ```sh
   NEW_VALUE="$(security find-generic-password -a dolr-ai -s gemini-api-key -w 2>/dev/null)"
   NEW_SHA="$(printf '%s' "$NEW_VALUE" | sha256sum | cut -c1-8)"
   ssh rishi-deploy@rishi-4 "echo '${NEW_VALUE}' | docker secret create GEMINI_API_KEY_${NEW_SHA} -"
   ssh rishi-deploy@rishi-4 "docker service update \
       --secret-rm GEMINI_API_KEY \
       --secret-add source=GEMINI_API_KEY_${NEW_SHA},target=GEMINI_API_KEY \
       yral-rishi-agent"
   ```
4. Wait for service to roll (3-5 min). Confirm health: `curl https://agent.rishi.yral.com/health`
5. Send a test chat message → verify Gemini call succeeds (Sentry: no `GEMINI_API_KEY missing` or 401 errors)
6. Disable the OLD key in AI Studio (after 24h grace to confirm nothing's still pinned to it):
   ```sh
   ssh rishi-deploy@rishi-4 "docker secret rm GEMINI_API_KEY"  # the old SHA-suffixed one
   ```

**Verify**:
- `/admin/llm-routing.json` shows `user_chat_main → gemini` still functioning
- Sentry: zero `429` or `401` from Gemini in the last 15 min
- Email digest tomorrow shows `user_chat_main` costs > 0 (proves real traffic hit Gemini)

**Notify**: Saikat (in case Gemini quota rate-limits change as a result), Anshuman (alternate LLM oncall).

---

### 2. `OPENROUTER_API_KEY`

**What**: OpenRouter bearer token. Used for NSFW-routed conversations + Tara-specialized routing.

**Where stored**:
- Operator: OpenRouter dashboard → Keys
- Production: Swarm secret `OPENROUTER_API_KEY`
- App reads: `app/config.py:OPENROUTER_API_KEY`

**Rotate**: same shape as Gemini above. Generate new key on OpenRouter dashboard → Keychain → Swarm secret roll.

**Keychain service name**: `openrouter-api-key`

**Verify**: trigger an NSFW conversation → check Sentry for OpenRouter 401s in the next 5 min.

**Notify**: OpenRouter dashboard sometimes throttles new keys for the first hour — schedule rotation NOT during peak alpha-soak hours.

---

### 3. `RUNPOD_VLLM_API_KEY`

**What**: Bearer token Saikat hands us for `saikat-llm-medium-fast.yral.com` (his vLLM serving for the `runpod_vllm` provider — 4 background processes route through it).

**Where stored**:
- Operator: GitHub Secret `RUNPOD_VLLM_API_KEY` + Keychain `runpod-vllm-api-key`
- Production: Swarm secret `RUNPOD_VLLM_API_KEY`
- App reads: `app/config.py:RUNPOD_VLLM_API_KEY`

**Rotate**: USE THE EXISTING WORKFLOW.

1. Saikat sends a new bearer token.
2. Update GitHub Secret value at https://github.com/dolr-ai/yral-rishi-agent/settings/secrets/actions
3. Update Keychain: `security add-generic-password -a dolr-ai -s runpod-vllm-api-key -w "<new>" -U`
4. Trigger the [Rotate runpod_vllm key workflow](https://github.com/dolr-ai/yral-rishi-agent/actions/workflows/rotate-runpod-vllm-key.yml) — typed confirmation `ROTATE RUNPOD KEY`, free-text reason.
5. The workflow creates a SHA8-suffixed Swarm secret, swaps it onto `yral-rishi-agent`, the service rolls one task at a time.

**Verify**:
- Workflow exits 0
- `/admin/llm-routing.json` shows runpod_vllm-routed processes still functioning
- The 4 background processes (`proactive_generation`, `streak_tracking`, etc.) all complete without 401 errors in the next 60 min

**Notify**: Saikat. He may need to confirm the previous bearer is fully revoked on his side.

---

### 4. `DATABASE_URL` / Patroni postgres password

**What**: postgres user password on the V2 Patroni cluster (rishi-4/5/6). The app's `DATABASE_URL` is `postgres://yral_agent:<password>@<floating-ip>:5432/yral_agent_db`.

**Where stored**:
- Operator: Keychain `v2-postgres-password` + Bitwarden backup
- Production: Patroni Spilo container reads from `/etc/patroni/secrets/postgres-password` (mounted from Swarm secret); the agent service reads `DATABASE_URL` from Swarm secret

**Rotate** — this is the riskiest rotation because it touches both Patroni AND the agent service:

1. **Take a `pg_dump` first** (Rule 9). Even though this isn't a schema change, you're touching credentials.
2. **Patroni-side rotation** (rolling through all 3 nodes):
   ```sh
   # On the LEADER
   ssh rishi-deploy@<leader-host> "docker exec -it $(patroni-cid) psql -U postgres -c \"ALTER USER yral_agent PASSWORD '<new>'\""
   ```
   The replication of the catalog table propagates to replicas automatically.
3. **Swarm secret rotation** (zero-downtime via roll):
   ```sh
   NEW_URL="postgres://yral_agent:<new-password>@<floating-ip>:5432/yral_agent_db"
   NEW_SHA="$(printf '%s' "$NEW_URL" | sha256sum | cut -c1-8)"
   ssh rishi-deploy@rishi-4 "echo '${NEW_URL}' | docker secret create DATABASE_URL_${NEW_SHA} -"
   ssh rishi-deploy@rishi-4 "docker service update \
       --secret-rm DATABASE_URL \
       --secret-add source=DATABASE_URL_${NEW_SHA},target=DATABASE_URL \
       yral-rishi-agent"
   ```
4. The service rolls one task at a time. Each task disconnects briefly (5-10s) then reconnects with the new credential.

**Verify**:
- `/admin/backup-health` shows GREEN
- `SELECT 1` via `psql` with the new credential succeeds
- Sentry: zero `password authentication failed for user "yral_agent"` errors in the next 15 min
- Both `cost_alerts` + `email_digest` background loops still tick (they each open their own pool)

**Notify**: Everyone. This is the most disruptive rotation. Schedule for a maintenance window.

**Rollback**: revert the Patroni ALTER USER (use the old password); roll the service back to the OLD `DATABASE_URL` Swarm secret name. Both halves must be reverted together.

---

### 5. `REDIS_URL` / Redis password

**What**: Auth password for the Redis primary + replicas (Sentinel topology on rishi-4/5/6). The app's `REDIS_URL` is `redis://:<password>@redis-primary:6379`.

**Where stored**:
- Operator: Keychain `v2-redis-password`
- Production: Swarm secret `REDIS_URL`
- App reads: `app/redis_config.py:get_redis_url()` (file-first, env fallback)

**Rotate**:

1. Update Redis password on ALL 3 nodes (primary first, then 2 replicas):
   ```sh
   # On the primary (Sentinel will continue routing reads here during the rotation)
   ssh rishi-deploy@<primary-host> "docker exec $(redis-cid) redis-cli CONFIG SET requirepass <new>"
   ssh rishi-deploy@<primary-host> "docker exec $(redis-cid) redis-cli AUTH <new>"
   ssh rishi-deploy@<primary-host> "docker exec $(redis-cid) redis-cli CONFIG REWRITE"
   ```
   Repeat on each replica.

2. Update Sentinel's stored password for `mymaster`:
   ```sh
   ssh rishi-deploy@<host> "docker exec $(sentinel-cid) redis-cli -p 26379 SENTINEL set mymaster auth-pass <new>"
   ```

3. Swarm-secret roll the agent:
   ```sh
   NEW_URL="redis://:<new-password>@redis-primary:6379"
   NEW_SHA="$(printf '%s' "$NEW_URL" | sha256sum | cut -c1-8)"
   echo "$NEW_URL" | ssh rishi-deploy@rishi-4 "docker secret create REDIS_URL_${NEW_SHA} -"
   ssh rishi-deploy@rishi-4 "docker service update \
       --secret-rm REDIS_URL \
       --secret-add source=REDIS_URL_${NEW_SHA},target=REDIS_URL \
       yral-rishi-agent"
   ```

**Verify**:
- WebSocket connections in `/admin/websockets.json` (if it exists) reconnect cleanly
- `cost_alerts` background loop ticks (it talks to Redis on every iteration)
- Sentry: zero `WRONGPASS` errors

**Notify**: Schedule during off-hours. Active WebSocket subscribers will briefly reconnect.

---

### 6. `REPLICATE_API_TOKEN`

**What**: Replicate bearer token for image generation (Flux). Used by `POST /api/v1/chat/conversations/{id}/images`.

**Where stored**:
- Operator: Keychain `replicate-api-token`
- Production: Swarm secret `REPLICATE_API_TOKEN`
- App reads: `app/config.py:REPLICATE_API_TOKEN`

**Rotate**: Replicate dashboard → API Tokens → create new → Keychain → Swarm secret roll. Same shape as Gemini.

**Verify**: trigger an image generation → check the image actually arrives on the WebSocket inbox.

**Notify**: nobody external — Replicate doesn't rate-throttle new tokens.

---

### 7. AWS S3 keys (Hetzner Object Storage)

**What**: S3-compatible access key + secret key for the Hetzner Object Storage bucket. Stores user-uploaded images + WAL-G backups.

**Where stored**:
- Operator: Hetzner Cloud → Object Storage → Credentials + Keychain `hetzner-s3-access-key` + `hetzner-s3-secret-key`
- Production: Swarm secrets `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `WALG_S3_ACCESS_KEY_ID` + `WALG_S3_SECRET_ACCESS_KEY` (separate keys for the app vs WAL-G, so a leak of one doesn't compromise the other)
- App reads: `app/config.py:AWS_*`
- WAL-G reads: env vars in the Patroni Spilo container

**Rotate**:

For the **app's S3 keys** (image uploads):
1. Hetzner Cloud → Object Storage → Credentials → create new
2. Keychain update both `hetzner-s3-access-key` + `hetzner-s3-secret-key`
3. Swarm-secret roll BOTH `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` in a single `docker service update`:
   ```sh
   ssh rishi-deploy@rishi-4 "docker service update \
       --secret-rm AWS_ACCESS_KEY_ID --secret-rm AWS_SECRET_ACCESS_KEY \
       --secret-add source=AWS_ACCESS_KEY_ID_${SHA},target=AWS_ACCESS_KEY_ID \
       --secret-add source=AWS_SECRET_ACCESS_KEY_${SHA},target=AWS_SECRET_ACCESS_KEY \
       yral-rishi-agent"
   ```
4. Disable old credentials in Hetzner after 24h.

For the **WAL-G keys** (backups):
1. Create separate Hetzner credentials specifically for WAL-G (DO NOT share keys with the app — leak of one shouldn't kill backup integrity)
2. Update the Patroni Spilo env via the Swarm secret + `docker service update` on the patroni service
3. **CRITICAL**: trigger an immediate `WAL-G restore drill` (see `scripts/walg_restore_drill.sh`) to confirm the new credentials can both read existing backups AND write new ones

**Verify**:
- App: upload a test image via chat → verify it appears via `/admin/influencers` or chat history
- WAL-G: `/admin/backup-health` shows GREEN; latest backup timestamp advances on the next hourly check

**Notify**: HIGH-stakes rotation. Schedule for a maintenance window. If WAL-G stops streaming, our PITR window shrinks minute-by-minute until fixed.

---

### 8. `JWT` (auth)

**What**: V2 does NOT sign JWTs — only verifies issuer (per CLAUDE.md `app/auth.py`: "JWT from Authorization header. No sig verify. Issuer check."). So there's no signing-key secret on the V2 side to rotate.

**Operator side**: the issuers (`https://auth.yral.com`, `https://auth.dolr.ai`, see `EXPECTED_ISSUERS` in `app/config.py`) sign with their own keys. If THOSE keys rotate, V2 keeps working because we only check the issuer claim — not signature.

If a new issuer is added (or an old one removed), update `app/config.py:EXPECTED_ISSUERS` + redeploy.

**Verify**: a fresh JWT from each issuer passes auth on `GET /api/v1/influencers`.

**Notify**: Anshuman (auth team) on issuer changes.

---

### 9. `ADMIN_KEY`

**What**: Master key for admin endpoints (`/admin/*`). Used to delete influencers + access internal dashboards.

**Where stored**:
- Operator: Keychain `admin-key-to-delete-influencer`
- Production: Swarm secret `ADMIN_KEY_TO_DELETE_INFLUENCER`
- App reads: `app/config.py:ADMIN_KEY`

**Rotate**: generate a new 32+ char random string. Same Swarm-secret roll pattern as Gemini.

**Verify**: hit `/admin/llm-routing` with the new key + the old key → new succeeds, old returns 401.

**Notify**: Rishi (only operator with admin access today). If anyone else has been issued the old key, revoke their access.

---

### 10. Langfuse credentials

**What**: `LANGFUSE_SECRET_KEY` + `LANGFUSE_PUBLIC_KEY` + ClickHouse + NextAuth + encryption-key + salt — see the memory entry `reference_keychain_service_names.md` for the full set.

**Where stored**: see the memory entry. ALL on Rishi's Keychain.

**Rotate**: Langfuse dashboard → Settings → API Keys → revoke old + create new. Same Swarm-secret roll pattern.

**Verify**: Langfuse dashboard shows traces flowing from V2 in the next 5 min.

**Notify**: nobody external.

---

### 11. `GOOGLE_CHAT_WEBHOOK_URL`

**What**: Google Chat webhook used by `cost_alerts` for ops-room notifications.

**Where stored**:
- Operator: Google Workspace admin → Webhook URL (in a private chat room)
- Production: Swarm secret `GOOGLE_CHAT_WEBHOOK_URL`
- App reads: `app/config.py:GOOGLE_CHAT_WEBHOOK_URL`

**Rotate**: Google Workspace admin → revoke + create new. Swarm-secret roll.

**Verify**: Trigger a synthetic cost alert (e.g. temporarily set `COST_ALERT_HOURLY_GEMINI_USD=0.01` for 5 min) → confirm webhook fires to the new URL.

**Notify**: anyone subscribed to the chat room.

---

### 12. `SENTRY_DSN`

**What**: Sentry project DSN.

**Where stored**:
- Operator: Sentry project settings → Client Keys (DSN)
- Production: Swarm secret `SENTRY_DSN`
- App reads: `app/infra.py:init_sentry()` via env var

**Rotate**: Sentry → Settings → Client Keys → create new DSN. Swarm-secret roll.

**Verify**: `sentry_sdk.capture_message("rotation test")` from a Python REPL on a server → confirm event appears in Sentry under the new project key.

**Notify**: nobody. Sentry doesn't rate-throttle new DSNs.

---

## Routine cadence

Annual cadence (calendar reminder for Rishi):

| Month | Secrets to rotate |
|---|---|
| March | GEMINI_API_KEY, OPENROUTER_API_KEY, REPLICATE_API_TOKEN |
| June | RUNPOD_VLLM_API_KEY (via workflow), AWS_*, WAL-G keys |
| September | DATABASE_URL, REDIS_URL, ADMIN_KEY |
| December | Langfuse credentials, GOOGLE_CHAT_WEBHOOK_URL, SENTRY_DSN |

Off-cadence (immediate): on suspected compromise, departing team member, accidental commit to a public repo. Run **gitleaks** if you suspect a leak — see `.github/workflows/gitleaks.yml` (Phase 21αβ.I-Sec1) or `pre-commit` hook locally.

## Adding a new secret to this runbook

When a new secret enters production, the PR adding it MUST also add a section here. PR template suggestion:

```markdown
## Adding a new production secret (`<NAME>`)

- [ ] Added to `app/config.py` via `_env("<NAME>")`
- [ ] Created Swarm secret on rishi-4 with current value
- [ ] Added section to `docs/runbooks/secret-rotation.md` (this file)
- [ ] Added GitHub Secret if a workflow needs it
- [ ] If high-value: added a dedicated `rotate-<name>.yml` workflow
- [ ] Tested rotation end-to-end at least once in staging
```

## What this runbook does NOT cover

- **Issuer-side JWT signing keys** — owned by the auth team (`https://auth.yral.com`)
- **Hetzner Cloud root credentials** — owned by Rishi personally; rotation = re-provisioning the project (not a routine op)
- **GitHub access tokens for individual users** — managed by each user, not by this repo
- **macOS Keychain master password** — owned by Rishi personally

## Related

- `.github/workflows/rotate-runpod-vllm-key.yml` — canonical Swarm-secret-roll pattern
- `.github/workflows/gitleaks.yml` — Phase 21αβ.I-Sec1 baseline scan
- `app/config.py` — all secret env var names + defaults
- `app/redis_config.py` — file-first URL pattern
- Session memory `feedback_keychain_service_names.md` — naming conventions
