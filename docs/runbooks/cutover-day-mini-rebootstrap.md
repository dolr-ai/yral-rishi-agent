# Cutover-day mini re-bootstrap — Option A runbook

**Phase 21αβ.H1.** Hard-cutover from chat-ai (rishi-1/2/3) to yral-rishi-agent (rishi-4/5/6) with a frozen-state ETL pass instead of the live 5-minute ETL loop.

This is a **paste-and-run** runbook. The intent: zero composing under pressure on cutover day. Every command is copyable. The sidecar approach is a mirror of the proven 2026-06-04 re-bootstrap; the Caddy snippet is the new piece.

## What this runbook does

1. Edge **rejects chat-ai traffic** with a "service moved — restart app" response → forces every mobile client to refresh Firebase Remote Config (which now points at agent.rishi.yral.com).
2. While chat-ai is frozen (no more writes), runs a **mini re-bootstrap** that pulls only the delta of conversations + messages written to chat-ai since the 2026-06-04 baseline.
3. **Validates** row counts + integrity end-to-end.
4. **Tears down** the sidecar.

Total wall: ~30 min. Database apply: ~2 min (delta is small).

## Pre-cutover checklist (T-24h)

- [ ] Mobile team has shipped Firebase Remote Config flip mechanism + tested in QA. The "service moved" 410 response from chat-ai must trigger a Firebase fetch + app retry on the new URL.
- [ ] Caddy snippet (below) reviewed by Rishi; sample 410 response tested in staging.
- [ ] V2 health checks all green (`/health`, `/health/db`, Patroni cluster, Sentinel, WAL-G archive timer).
- [ ] V2 last deploy `:stable` tag verified pointing at known-good SHA (`docker service inspect yral-rishi-agent --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'`).
- [ ] Cutover window confirmed with team. Default target: **Sunday 03:00 IST** (lowest chat-ai 24h traffic per Sentry).
- [ ] Pre-cutover V2 pg_dump scheduled per Rule 9.

## Pre-cutover checklist (T-1h)

- [ ] SSH carve-out re-authorized by Rishi in conversation (per `feedback_agent_safety_and_24x7_access` allowlist; chat-ai hosts rishi-1/2/3 as `deploy` user, read-only commands only).
- [ ] Snapshot V2 pre-cutover state:
  ```bash
  ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@<rishi-4-ip> \
    "pg_dump -h /data/patroni -U postgres -d yral_rishi_agent -Fc -Z 6 \
       -f /home/rishi-deploy/yral-backups/pre-cutover-v2-snapshot-$(date +%Y%m%d-%H%M%S).dump"
  ```
- [ ] Capture chat-ai pre-cutover row counts (read-only):
  ```bash
  ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-2-leader-ip> \
    "psql -h /data/patroni -U postgres -d chat_ai_db -c \
       'SELECT (SELECT count(*) FROM conversations) AS conv, \
               (SELECT count(*) FROM messages)      AS msg, \
               (SELECT count(*) FROM ai_influencers) AS infl;'"
  ```
  Save the three numbers — needed for the post-apply validation diff.

---

## Step 1 — Edge cutover (Caddy 410 on rishi-1/2)

**Authorize first.** Rishi: confirm in conversation. This step makes chat-ai unreachable from mobile clients.

The snippet replaces the existing `reverse_proxy` block for the chat-ai upstream on rishi-1/2 with a `respond 410` that includes a JSON body the mobile client recognizes (signals "service moved — restart and refetch Firebase config"). The 410 is intentional: it's the "Gone" status, distinct from a transient 502/503, so retry-loops give up cleanly.

```caddy
# /etc/caddy/snippets/chat-ai-410.conf — applied during cutover only
#
# chat-ai backend is intentionally retired here. The mobile app fetches
# Firebase Remote Config on this 410, learns the new URL
# (agent.rishi.yral.com), and reconnects without app-restart UX in newer
# builds; older builds show the "service moved — please restart" sheet.

@chat-ai-host host chat-ai.yral.com
handle @chat-ai-host {
    respond `{"error":"service_moved","new_url":"https://agent.rishi.yral.com","message":"Please restart the app."}` 410 {
        close
    }
    header Content-Type application/json
}
```

**Apply on rishi-1 and rishi-2 simultaneously** (these are the two edge Caddy nodes per `project_infra_actual_state_day9`):

```bash
# On rishi-1 and rishi-2 (run in parallel; coordinator pastes both):
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-cutover.$(date +%Y%m%d-%H%M%S)
sudo $EDITOR /etc/caddy/Caddyfile        # include chat-ai-410.conf snippet
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -sI https://chat-ai.yral.com/      # expect: HTTP/2 410
```

**Verify** from a non-yral network (avoid in-DC routing): `curl -i https://chat-ai.yral.com/health` should return 410 with the JSON body within 30s of reload on both edges.

**Rollback for this step**: `sudo cp /etc/caddy/Caddyfile.pre-cutover.<TS> /etc/caddy/Caddyfile && sudo systemctl reload caddy`. Returns to the pre-cutover reverse_proxy in seconds.

---

## Step 2 — Confirm chat-ai is frozen (no new writes)

Wait 60 seconds after the Caddy reload, then on the chat-ai leader (rishi-2):

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-2-leader-ip> \
  "psql -h /data/patroni -U postgres -d chat_ai_db -c \
     \"SELECT now() - max(created_at) AS since_last_msg FROM messages;\""
```

If `since_last_msg` is > 60s, chat-ai is frozen. If new rows are still landing, **STOP** — the Caddy snippet didn't catch all client paths. Investigate before proceeding.

---

## Step 3 — Dump chat-ai (leader)

The 2026-06-04 baseline learned: never dump from a replica (recovery-conflict killed the long-running pg_dump). Always dump from the leader.

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-2-leader-ip>
# Inside that SSH session — read-only pg_dump only:
pg_dump -Fc -Z 6 -h /data/patroni -U postgres -d chat_ai_db \
  --no-owner --no-acl \
  -f /tmp/chat-ai-cutover.dump
ls -lh /tmp/chat-ai-cutover.dump   # sanity-check size — expect ~500-600 MB
exit
```

Then copy to the V2 manager (rishi-4):

```bash
scp -i ~/.ssh/rishi-hetzner-ci-key \
  deploy@<rishi-2-leader-ip>:/tmp/chat-ai-cutover.dump \
  rishi-deploy@<rishi-4-ip>:/tmp/chat-ai-cutover.dump
```

---

## Step 4 — Sidecar pg16 + restore

chat-ai runs pg16; V2 runs pg15. A pg15 client can't read a pg16 dump archive. The sidecar bridges the version gap. Mirror of the 2026-06-04 recipe.

On **rishi-4**:

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@<rishi-4-ip>

# 1. Spin up an ephemeral pg16 container on the V2 data-plane network:
docker run -d --name cutover-sidecar \
  --network yral-v2-data-plane \
  -e POSTGRES_PASSWORD=sidecar-temp \
  -v /tmp/chat-ai-cutover.dump:/tmp/dump:ro \
  postgres:16-alpine

# 2. Wait for it to come up (~7s on the baseline):
until docker exec cutover-sidecar pg_isready -U postgres > /dev/null 2>&1; do sleep 1; done

# 3. Create the target DB and restore:
docker exec cutover-sidecar createdb -U postgres chat_ai_db
docker exec cutover-sidecar pg_restore \
  -U postgres -d chat_ai_db --no-owner --no-acl \
  /tmp/dump
# Baseline wall: ~68s
```

---

## Step 5 — Apply delta to V2

Run the apply script in a python:3.12-alpine container on the **same data-plane network** so it can reach both the sidecar (`cutover-sidecar:5432`) and the V2 leader (Patroni service DNS). asyncpg, explicit-column INSERT-SELECT, bare `ON CONFLICT DO NOTHING`, orphan-filter for messages.

Save the apply script on rishi-4 as `/tmp/apply.py`:

```python
# /tmp/apply.py — cutover-day mini re-bootstrap delta apply.
# Mirror of the 2026-06-04 throwaway script. Bare ON CONFLICT DO NOTHING
# handles BOTH PK and the partial unique index idx_unique_user_influencer
# (the 2026-06-04 finding — id-arbiter alone wasn't enough).
import asyncio, asyncpg, os

SIDECAR_DSN = "postgresql://postgres:sidecar-temp@cutover-sidecar:5432/chat_ai_db"
V2_DSN      = os.environ["V2_DSN"]   # injected via docker run -e

AI_INFLUENCERS_COLS = [
    "id","name","handle","description","avatar_url","banner_url",
    "personality_prompt","voice_id","is_active","created_at","updated_at",
    "archetype","gender","language_primary","ethnicity","age_bracket","tier",
]
CONVERSATIONS_COLS = [
    "id","user_id","influencer_id","title","metadata","created_at",
    "updated_at","last_message_at",
]
MESSAGES_COLS = [
    "id","conversation_id","role","content","metadata","tokens_in",
    "tokens_out","model","provider","cost_usd","latency_ms","error",
    "created_at","updated_at","parent_message_id",
]

async def copy_table(src, dst, table, cols, orphan_filter=None):
    col_list = ", ".join(cols)
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    src_rows = await src.fetch(f"SELECT {col_list} FROM {table}")
    if orphan_filter:
        kept = [r for r in src_rows if orphan_filter(r)]
        print(f"{table}: {len(src_rows)} source, {len(src_rows) - len(kept)} orphans filtered, applying {len(kept)}")
        src_rows = kept
    inserted = 0
    async with dst.transaction():
        for row in src_rows:
            result = await dst.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                *[row[c] for c in cols],
            )
            if result.endswith(" 1"):
                inserted += 1
    print(f"{table}: inserted {inserted}")

async def main():
    src = await asyncpg.connect(SIDECAR_DSN)
    dst = await asyncpg.connect(V2_DSN)
    try:
        await copy_table(src, dst, "ai_influencers",  AI_INFLUENCERS_COLS)
        await copy_table(src, dst, "conversations",   CONVERSATIONS_COLS)
        v2_conv_ids = {r["id"] for r in await dst.fetch("SELECT id FROM conversations")}
        await copy_table(
            src, dst, "messages", MESSAGES_COLS,
            orphan_filter=lambda r: r["conversation_id"] in v2_conv_ids,
        )
    finally:
        await src.close()
        await dst.close()

asyncio.run(main())
```

**Important — verify column lists before running.** The lists above are the 2026-06-04 baseline. If chat-ai or V2 added columns since, the lists may need an update. Quick sanity check (run on V2 leader before the apply):

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'ai_influencers' ORDER BY ordinal_position;
-- repeat for conversations, messages
```

The script uses **explicit columns from chat-ai's subset**; any V2-only extras retain their defaults. That's what made the 2026-06-04 run safe.

Then run the apply container:

```bash
# V2 DSN — get from the V2 secret store, NOT from this file:
export V2_DSN="postgresql://yral_app:$(cat /run/secrets/v2_db_password)@yral-rishi-agent_db:5432/yral_rishi_agent"

docker run --rm \
  --network yral-v2-data-plane \
  -v /tmp/apply.py:/app/apply.py:ro \
  -e V2_DSN \
  python:3.12-alpine \
  sh -c "pip install --quiet asyncpg && python /app/apply.py"
# Baseline wall: 0.4s + 15.7s + 207s = ~4 min on the full re-bootstrap.
# Delta cutover is much smaller — expect <60s total.
```

---

## Step 6 — Validate (Layer 1/2/3)

**Layer 1 — row counts.** On V2 leader:

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@<rishi-4-ip> \
  "psql -h /data/patroni -U postgres -d yral_rishi_agent -c \
     'SELECT (SELECT count(*) FROM conversations) AS conv, \
             (SELECT count(*) FROM messages)      AS msg, \
             (SELECT count(*) FROM ai_influencers) AS infl;'"
```

Compare against the pre-cutover chat-ai counts captured at T-1h. V2 should have **≥** chat-ai's counts on all three (V2-native rows exist; V2 should also have absorbed all of chat-ai's, modulo Option A's 318 skipped duplicates).

**Layer 2 — Option A audit.** Confirm the skip count is still bounded and explained:

```sql
-- duplicate (user, influencer) skips — should match 2026-06-04's 318 + any new ones during cutover delta:
SELECT count(*) FROM (
  SELECT user_id, influencer_id, count(*) FROM conversations
  GROUP BY 1,2 HAVING count(*) > 1
) x;
-- Expect: 0 in V2 (the partial unique index enforces). The "skips" are in the source dump, not in V2.
```

**Layer 3 — orphan check.** No new orphans should have been inserted (the orphan_filter dropped them at apply time):

```sql
SELECT count(*) FROM messages m
LEFT JOIN conversations c ON c.id = m.conversation_id
WHERE c.id IS NULL;
-- Expect: 10 (the pre-existing V2 orphans noted in project_re_bootstrap_complete_2026_06_05).
-- If higher, the orphan_filter missed cases — investigate before declaring success.
```

---

## Step 7 — Smoke test from mobile + browser

- [ ] Mobile QA: open the app (cold start), confirm Firebase Remote Config refreshed, chat opens against agent.rishi.yral.com, send + receive a chat message.
- [ ] curl `/health` and `/health/db` on agent.rishi.yral.com from outside the DC.
- [ ] Spot-check a chat-ai user that had recent activity: their last 5 messages should be visible in V2.

---

## Step 8 — Tear-down

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@<rishi-4-ip>
docker stop cutover-sidecar
docker rm cutover-sidecar
rm /tmp/chat-ai-cutover.dump /tmp/apply.py
exit
```

On rishi-2:
```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-2-leader-ip>
rm /tmp/chat-ai-cutover.dump
exit
```

---

## Rollback (if any validation step fails)

The cutover can be reversed at any step **before** Step 8 tear-down. After tear-down, the rollback is "promote chat-ai again" — slower, but the dump + V2 snapshot are still on disk.

### Rollback A — chat-ai still has all the data (preferred)

If validation fails after Steps 1-7 but chat-ai's data has NOT been touched (Steps 1-6 only read from chat-ai), simply restore the Caddy config to reverse-proxy chat-ai again:

```bash
# On rishi-1 and rishi-2:
sudo cp /etc/caddy/Caddyfile.pre-cutover.<TS> /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -sI https://chat-ai.yral.com/health   # expect: 200 OK from chat-ai again
```

Mobile clients still on the old URL (no Firebase refresh yet) keep working. Clients that already moved to agent.rishi.yral.com need a Firebase Remote Config flip-back to point back at chat-ai.yral.com.

### Rollback B — V2 data corrupted by the apply

Restore V2 from the pre-cutover snapshot taken at T-1h:

```bash
# Coordinated with Patroni — DO NOT pg_restore directly on the leader's data dir.
# Use the WAL-G PITR or restore-into-fresh-cluster path documented in
# docs/BACKUP-RESTORE-DRILL-2026-06-04.md.
```

Then re-apply Rollback A.

---

## Why this is safe

1. **chat-ai DB is read-only during the cutover** — every command in Steps 3-6 reads from chat-ai and writes only to the sidecar (ephemeral) and to V2. If anything goes wrong, chat-ai's data is untouched.
2. **The apply is idempotent** — bare `ON CONFLICT DO NOTHING` means re-running the script doesn't double-insert. If Step 5 fails partway through, you can re-run it.
3. **The orphan filter is conservative** — it drops messages whose conv was Option-A-skipped. Filter is read once at the start of the messages pass; safe for the duration.
4. **Caddy 410 is reversible in seconds** — `cp` back the saved Caddyfile, `systemctl reload`. Faster than rolling back any code deploy.
5. **The V2 pre-cutover dump (T-1h) is the worst-case backstop.** WAL-G PITR is the second backstop.

---

## Cross-references

- `docs/BACKUP-RESTORE-DRILL-2026-06-04.md` — the restore-from-pg_dump drill that proves Rollback B works.
- `docs/WALG-RECOVERY-RUNBOOK.md` — WAL-G PITR for sub-snapshot recovery.
- `docs/DEPLOY.md` — `:stable` tag invariant + auto-rollback for the V2 service itself.
- `project_re_bootstrap_complete_2026_06_05` (memory) — the 2026-06-04 full re-bootstrap baseline this runbook mirrors.
- `project_etl_option_a_conflict_handling` (memory) — the Option A semantics (318 duplicate-skip + orphan filter).
- PROGRESS.md 21αβ.H1 — this runbook closes action item (2) of three. Items (1) Caddy snippet is embedded here; (3) cutover-day window confirm is Rishi-driven.
