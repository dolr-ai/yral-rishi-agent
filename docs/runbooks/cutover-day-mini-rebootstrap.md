# Cutover-day mini re-bootstrap — Option A runbook

**Phase 21αβ.H1.** Soft cutover from chat-ai (rishi-1/2/3) to yral-rishi-agent (rishi-4/5/6) via a Firebase Remote Config flip, followed by a mini re-bootstrap that catches up the chat-ai delta. chat-ai routing stays untouched at the edge for at least 7 days so a Firebase rollback can revert traffic instantly if V2 hits a P0 in the first week of prod.

This is a **paste-and-run** runbook. The intent: zero composing under pressure on cutover day. Every command is copyable. The sidecar approach mirrors the proven 2026-06-04 re-bootstrap.

## What this runbook does

1. **Flips Firebase Remote Config** so the mobile app's `CHAT_BASE_URL` points at `agent.rishi.yral.com` for the prod audience. Clients pick this up on next config-fetch (~5-15 min for most; an app-restart for the rest).
2. **Mini re-bootstrap at T+30min** catches up everything written to chat-ai between the 2026-06-04 baseline and the cutover instant.
3. **Monitors chat-ai access logs** during the soft transition (chat-ai stays reachable so slow-to-refresh clients keep working until their config-fetch lands).
4. **Optional T+7d decommission** (separate gated section at the bottom) — once V2 has been live ≥7 days clean AND chat-ai traffic has tapered to <1% of pre-cutover volume AND Rishi approves, the chat-ai Caddy route can be retired with the 410 snippet at the end of this file.

Total cutover-day wall: ~30 min for the Firebase flip + monitoring window opens. Mini re-bootstrap apply: ~2 min.

## Why no Caddy change on cutover day

Rolling back the Firebase Remote Config flip is **instant** — change a single config value, all clients pick it up on next fetch. Rolling back a Caddy 410 would require reverting the snippet on rishi-1 AND rishi-2 under whatever stress conditions caused the rollback. Keeping chat-ai routed-and-reachable for the first 7 days preserves the cheap rollback path.

## Pre-cutover checklist (T-24h)

- [ ] Mobile team has shipped the Firebase Remote Config-driven `CHAT_BASE_URL` (verified in QA: setting the RC value to `https://agent.rishi.yral.com` makes a freshly-launched app talk to V2).
- [ ] Firebase Remote Config flip mechanism documented (which condition / audience / fetch-interval applies to the prod cohort).
- [ ] V2 health checks all green (`/health`, `/health/db`, Patroni cluster, Sentinel, WAL-G archive timer).
- [ ] V2 last deploy `:stable` tag verified pointing at known-good SHA (`docker service inspect yral-rishi-agent --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'`).
- [ ] Cutover window confirmed with team. Default target: **Sunday 03:00 IST** (lowest chat-ai 24h traffic per Sentry).
- [ ] Pre-cutover V2 pg_dump scheduled per Rule 9.
- [ ] chat-ai access-log tail confirmed reachable (Caddy on rishi-1/2 — `journalctl -u caddy` or wherever the access logs go). We'll need this during monitoring.

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
- [ ] Note the wall-clock time you intend to flip Firebase Remote Config — this is **T-0**.

---

## Step 1 — Firebase Remote Config flip (T-0)

**The cutover mechanism.** No Caddy change on rishi-1/2. chat-ai stays reachable.

In the Firebase console:

1. Open the project for the mobile app.
2. Go to **Remote Config**.
3. Edit the `CHAT_BASE_URL` parameter (or whatever the mobile contract calls it — confirm with mobile team).
4. Change the **prod audience** condition's value from `https://chat-ai.yral.com` (or whatever the chat-ai URL is) to:
   ```
   https://agent.rishi.yral.com
   ```
5. **Publish**.

Clients pick this up on their next config-fetch. Most apps refetch on cold start; the Firebase SDK also has a periodic background refresh (typically 12h default; mobile team may have set it tighter for the cutover window — confirm).

**Verify**:
- Mobile QA: cold-start the app, open chat, send a message. Confirm the request hits `agent.rishi.yral.com` (visible in V2's access logs / Langfuse trace).
- A small subset of staff-account devices should be doing this first (5-15 min window before the prod audience is opened).

**Rollback at this step**: re-edit the RC parameter back to the chat-ai URL and republish. Clients revert on next fetch. **This is the cheap rollback the rest of this runbook preserves.**

---

## Step 2 — Mark the snapshot instant (T-0 + ~30min)

We do NOT freeze chat-ai. Slow-to-refresh clients keep writing to it during the soft transition. The mini re-bootstrap captures everything written to chat-ai **up to a chosen instant** — the rest is handled in the monitoring section below.

The chosen instant is when you start the pg_dump (Step 3). Note this wall-clock time — anything written to chat-ai after this is **NOT** in this mini re-bootstrap (it would need a follow-up re-bootstrap OR be accepted as drift; see monitoring section).

Sanity-check that chat-ai is still healthy (no freeze expected):

```bash
ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-2-leader-ip> \
  "psql -h /data/patroni -U postgres -d chat_ai_db -c \
     \"SELECT now() AS snapshot_instant, max(created_at) AS last_msg FROM messages;\""
```

Save the `snapshot_instant` — this is the cutover-day mini re-bootstrap's "as-of" timestamp. Used in validation + monitoring.

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

Note: pg_dump on a live database is point-in-time consistent — the dump represents chat-ai's state at the moment `pg_dump` started. Writes that land in chat-ai during the dump run are NOT in the dump.

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

Note: chat-ai's counts have moved on slightly since T-1h (slow-to-refresh clients still writing). What V2 reflects is the chat-ai state at **`snapshot_instant`** (Step 2), not at "now". Any chat-ai rows newer than `snapshot_instant` are the tail handled in monitoring.

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

## Step 8 — Tear-down (cutover-day artifacts only)

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

**chat-ai service itself stays running.** No Caddy change, no DB stop. We need it reachable for the next 7 days.

---

## T-0 → T+7d — Soft transition monitoring

For the first 7 days after the Firebase Remote Config flip, chat-ai stays reachable and some slow-to-refresh clients will continue writing to it. This window is monitored, not frozen.

### Daily (or at least every 24h) checks

1. **chat-ai write volume**: tail the chat-ai Caddy access logs (rishi-1 / rishi-2) and count POST requests per hour. Expected: starts at ~pre-cutover volume, halves within ~6h (typical Firebase fetch cadence), should be <5% of pre-cutover by T+24h and <1% by T+7d.
   ```bash
   ssh -i ~/.ssh/rishi-hetzner-ci-key deploy@<rishi-1-ip> \
     "sudo journalctl -u caddy --since '1 hour ago' | grep -c 'POST.*chat-ai'"
   ```
2. **chat-ai DB writes**: count messages inserted into chat-ai per hour. Should track the access-log POST count.
   ```sql
   SELECT date_trunc('hour', created_at) AS hr, count(*) FROM messages
   WHERE created_at > now() - interval '24 hours'
   GROUP BY 1 ORDER BY 1 DESC;
   ```
3. **V2 health + cost dashboard** — same checks as any other deploy day (Phase 19.6 dashboard, email digest, Sentry).

### If concerning chat-ai writes continue past T+24h

Surface to Rishi for decision:
- **Extend monitoring.** Most cases — the Firebase fetch cadence has long-tail clients that haven't restarted. Keep watching.
- **Interim mini re-bootstrap.** Re-run Steps 2-8 of this runbook to capture the tail. Each interim re-bootstrap is the same shape — sidecar, apply, validate, tear down.
- **Accept the orphan.** If the absolute count is small (<100 messages, <10 users) and stable, the conversations in chat-ai's tail can be left unmigrated. Document which user_ids fell into this bucket in case of support tickets.

### Rollback during the soft transition window

If V2 hits a P0 in the first 7 days and a rollback is needed:

1. **Firebase Remote Config flip-back** — revert `CHAT_BASE_URL` to the chat-ai value. Clients pick it up on next fetch. chat-ai is still routed normally at the edge.
2. **chat-ai is back to authoritative** — no V2 → chat-ai data migration needed (the V2-native writes that happened during the V2-live window are stranded in V2, but the user-visible service is back on chat-ai's data).
3. **Triage V2 in parallel**, no time pressure on the rollback itself.

If V2 fails post-T+7d (after Caddy decommission below), the rollback is heavier — Firebase flip + Caddy revert. That's why the gates below are explicit.

---

## Rollback (data corruption during cutover day, before T+7d decommission)

The cutover-day mini re-bootstrap itself can be reversed:

### Rollback A — V2 data corrupted by the apply (Firebase still flipped)

Restore V2 from the pre-cutover snapshot taken at T-1h:

```bash
# Coordinated with Patroni — DO NOT pg_restore directly on the leader's data dir.
# Use the WAL-G PITR or restore-into-fresh-cluster path documented in
# docs/BACKUP-RESTORE-DRILL-2026-06-04.md.
```

Then flip Firebase Remote Config back to chat-ai (instant) while V2 is being restored, so users keep working in the meantime.

### Rollback B — Apply script wrong / partial / interrupted

The apply is idempotent (bare `ON CONFLICT DO NOTHING`) — just re-run Step 5 with the same dump. If the dump itself is corrupted, re-run Step 3 + 4 + 5.

---

## Why this is safe

1. **chat-ai DB is read-only from V2's perspective during the cutover** — every command in Steps 3-6 reads from chat-ai and writes only to the sidecar (ephemeral) and to V2. chat-ai's own writes (from slow-to-refresh clients) continue normally; we never touch them.
2. **The apply is idempotent** — bare `ON CONFLICT DO NOTHING` means re-running the script doesn't double-insert. If Step 5 fails partway through, you can re-run it.
3. **The orphan filter is conservative** — drops messages whose conv was Option-A-skipped. Filter is read once at the start of the messages pass; safe for the duration.
4. **Firebase Remote Config flip-back is instant** — change one config value, all clients revert on next fetch. No edge-config revert needed under stress; chat-ai is still routed at Caddy.
5. **The V2 pre-cutover dump (T-1h) is the worst-case backstop.** WAL-G PITR is the second backstop.
6. **chat-ai stays reachable for ≥7 days** — preserves the cheap rollback path. Decommission only happens after the 3 gates below.

---

# T+7 days — chat-ai Caddy decommission (GATED)

**This is a SEPARATE operation from the cutover.** Do not perform it on cutover day.

The Firebase Remote Config flip is the cutover mechanism; the Caddy 410 below is the "we no longer need chat-ai reachable" cleanup. The two are deliberately separated so the first week of prod V2 has the cheap rollback path (Firebase flip-back) available.

## All three gates must clear before running the snippet

- **Gate 1: V2 has been live to real prod users for ≥7 days with zero P0 incidents.**
  - Check Sentry, the email digest, the dashboard. Define "P0" with team if not already defined.
- **Gate 2: chat-ai traffic has dropped to near-zero.**
  - <1% of pre-cutover POST volume on chat-ai's Caddy access logs (tail per the monitoring section above).
  - <1% of pre-cutover write rate on chat-ai's messages table.
  - At least 48 hours at this level before this gate counts as satisfied (one-off troughs don't count).
- **Gate 3: Rishi explicit approval.**
  - In conversation: "decommission chat-ai routing". Not "looks fine to me." Explicit verb.

**If any gate fails, do not proceed.** Extend the monitoring window or surface the specific failed gate.

## Optional final mini re-bootstrap (recommended)

Before retiring chat-ai's route, run one last mini re-bootstrap to capture any tail writes (Steps 2-8 of this runbook). Compare row counts before/after — if the tail is large enough to be worth running, do it; if it's tiny (<10 rows) accept it as orphan.

## The Caddy snippet (apply ONLY after all 3 gates clear)

Same snippet as the original H1 design — relocated here so it can't be accidentally applied on cutover day.

```caddy
# /etc/caddy/snippets/chat-ai-410.conf — applied at T+7d decommission only
#
# chat-ai backend is intentionally retired here. Any client still hitting
# this URL has either failed to refresh Firebase Remote Config or is
# stale/malicious. The 410 ("Gone") signals "service moved permanently"
# distinct from a transient 502/503 — retry-loops give up cleanly.
# Older mobile builds with a fallback path show the "service moved —
# please restart" sheet; newer builds fetch Firebase RC on the 410 and
# reconnect to the new URL transparently.

@chat-ai-host host chat-ai.yral.com
handle @chat-ai-host {
    respond `{"error":"service_moved","new_url":"https://agent.rishi.yral.com","message":"Please restart the app."}` 410 {
        close
    }
    header Content-Type application/json
}
```

**Apply on rishi-1 and rishi-2** (the two edge Caddy nodes per `project_infra_actual_state_day9`):

```bash
# On rishi-1 and rishi-2:
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-decommission.$(date +%Y%m%d-%H%M%S)
sudo $EDITOR /etc/caddy/Caddyfile        # include chat-ai-410.conf snippet
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -sI https://chat-ai.yral.com/      # expect: HTTP/2 410
```

**Verify** from a non-yral network (avoid in-DC routing): `curl -i https://chat-ai.yral.com/health` should return 410 with the JSON body within 30s of reload on both edges.

## Post-decommission

- After 410 has been live for ~24h with no support tickets and no Sentry spikes from the mobile crash-on-410 path: the chat-ai service itself (Rust binary on rishi-1/2/3) and chat-ai's Patroni cluster can be candidates for shutdown. **That's another, larger gated decision** — out of scope for this runbook.
- The Caddy snippet itself is reversible in seconds (`cp` back the saved Caddyfile + reload) if the 410 surfaces an unexpected issue.

---

## Cross-references

- `docs/BACKUP-RESTORE-DRILL-2026-06-04.md` — the restore-from-pg_dump drill that proves Rollback B works.
- `docs/WALG-RECOVERY-RUNBOOK.md` — WAL-G PITR for sub-snapshot recovery.
- `docs/DEPLOY.md` — `:stable` tag invariant + auto-rollback for the V2 service itself.
- `project_re_bootstrap_complete_2026_06_05` (memory) — the 2026-06-04 full re-bootstrap baseline this runbook mirrors.
- `project_etl_option_a_conflict_handling` (memory) — the Option A semantics (318 duplicate-skip + orphan filter).
- PROGRESS.md 21αβ.H1 — this runbook closes action items (1) Caddy snippet (relocated to T+7d decommission) and (2) mini re-bootstrap procedure. Item (3) cutover-day window confirm is Rishi-driven.
