# Post-mortem — Patroni leaderless cascade, 2026-07-21

**Status:** RESOLVED (public services back to 200 at ~12:28 UTC 07-21).
**Author:** Diagnostic session (read-only), reporting to Session 6.
**Severity:** SEV-1 — agent.rishi.yral.com + analytics.rishi.yral.com + amorae.ai all 503 for ~2h40m.
**One-line root cause:** All three Patroni nodes auto-rebooted for a kernel upgrade in the same minute (unattended-upgrades, recurring ~weekly Monday window); leadership was orphaned onto a degraded rishi-5 which ~27h later lost its DCS lock amid sync-replication-key contention, and synchronous-mode's safety guard correctly refused to auto-promote either survivor → extended leaderless outage that required a manual failover.

---

## A) Timeline (UTC)

| Time (UTC) | Node / event | Leader after | External symptom |
|---|---|---|---|
| 07-07 03:11 | rishi-4 acquires leader (TL 43) — steady state for ~13 days | rishi-4 | none |
| **07-20 06:36:25** | unattended-upgrades begins tearing down services (etcd/patroni stop); rishi-4 loses lock; rishi-5 grabs it → **TL 44** | rishi-5 | none |
| 07-20 06:38:01 | rishi-5 re-elects amid teardown → **TL 45** | rishi-5 | none |
| **07-20 06:48 (08:48 CEST)** | **ALL THREE hosts reboot simultaneously** for kernel 6.8.0-134 → 6.8.0-136 | — (brief total loss) | brief |
| 07-20 06:49–06:50 | hosts back; Patroni restarts; rishi-5 acquires leader → **TL 46** (06:50:45); rishi-4 & rishi-6 follow rishi-5 | rishi-5 | brief |
| 07-20 06:50–06:56 | overlay-dns watchdog auto-heals langfuse-web, analytics, patroni-rishi-4 drift; apps back (yral-rishi-agent 2/2) | rishi-5 | recovered |
| 07-20 06:50 → 07-21 09:42 | **~27h limp:** rishi-5 degraded leader, rishi-4 sync_standby, rishi-6 replica. Functional, zero redundancy margin. rishi-5 logs sync-key contention throughout. | rishi-5 | none |
| **07-21 09:42–09:44** | rishi-5 logs `Synchronous replication key updated by someone else` persistently → loses DCS lock (~09:44) | — | — |
| **07-21 09:45:05** | rishi-4 **and** rishi-6 deadlock: `following a different leader because i am not the healthiest node`; neither auto-promotes. `cluster_unlocked: true`. | **none** | **503 begins** |
| 07-21 09:45 → 12:27 | leaderless ~2h40m; apps burn connections retrying → `TooManyConnectionsError` (symptom) | none | **503** |
| **07-21 12:27:41** | Session 6 `POST /failover` promotes rishi-6 → **TL 48**; DB leader restored | rishi-6 | recovering |
| 07-21 12:27:49 | rishi-5 tries to rewind/rejoin rishi-6, hits `password authentication failed for user "postgres"` / pg_hba reject → **"start failed"** (stuck) | rishi-6 | — |
| 07-21 ~12:28+ | rishi-4 wiped + re-basebackup'd from rishi-6 (`remove_data_directory_on_diverged_timelines`); converged TL 48, sync_standby, lag 0 | rishi-6 | **200 restored** |

Current state (verified this session): rishi-6 leader TL 48; rishi-4 sync_standby TL 48 lag 0; **rishi-5 replica "start failed" (still down)**.

### The recurring pattern (this was NOT a one-off)
Reboot history is identical across all three nodes and lands on ~Monday-morning CEST with a climbing kernel version — the signature of Ubuntu `unattended-upgrades` auto-reboot:

- May 18 07:16 (6.8.0-117), Jun 1 07:26 (-124), Jun 8 07:18 (-124), Jul 6 07:07 (-134), **Jul 20 08:48 (-136)**.

Each maps onto a timeline switch in DCS `/history` (e.g. TL 22 @ 06-01 05:26, TL 26 @ 06-08 05:18, TL 37 @ 07-06 05:10). **The cluster has taken a simultaneous all-node reboot roughly monthly for three months.** Most times it self-heals in minutes; this time it deadlocked.

---

## B) Root cause verdict

Multi-causal, in order of contribution:

1. **Trigger:** all three Patroni nodes ran unattended-upgrades and rebooted in the same minute (07-20 06:48 UTC). No reboot staggering → no node stayed up to hold a stable lock through the window.
2. **Latent fragility:** the reboot left rishi-5 as leader, but a *degraded* one (continuous sync-replication-key contention with the DCS). The cluster limped for ~27h with no redundancy margin.
3. **The outage itself:** when rishi-5 finally dropped its lock (07-21 09:44), Patroni's `synchronous_mode` + `maximum_lag_on_failover` safety guard (the "not the healthiest node" check) **correctly** refused to auto-promote either survivor to avoid data loss — but with no operator alert, so it deadlocked leaderless for 2h40m until Session 6 manually promoted rishi-6.

The shm/DiskFull issue (PR #457) and a Postgres connection storm were **NOT** causes (see D, and Q5 below).

---

## C) Was the watchdog silent, or ignored?

**Neither silent nor ignored — it fired and worked, but it has a blind spot.** The deployed service is `overlay-watchdog_overlay-watchdog` (`ghcr.io/dolr-ai/yral-overlay-dns-watchdog`), running on rishi-5. During the 07-20 reboot it:
- detected replica drift for patroni-rishi-4, langfuse-web, analytics, metabase, yral-rishi-agent;
- auto-healed langfuse-web and analytics via force-update;
- logged `overlay-dns alias patroni-rishi-4 RECOVERED` to **Sentry** (visible: issue @ 07-20 06:54, count 2).

**But it only monitors swarm replica counts + overlay DNS. It has NO Patroni leader-presence / `cluster_unlocked` check.** A Patroni container running with *no elected leader* looks perfectly healthy to a replica-count watchdog. So the real outage (09:45–12:27) produced **zero alerts** — Sentry queries for `leader`, `no leader`, `watchdog` return nothing for the window. That is precisely why no one was paged.

---

## D) Was rishi-5 pre-broken?

**No.** Earliest `password authentication failed for user "postgres"` in rishi-5's Patroni log is **2026-07-21T12:27:49Z** — eight seconds *after* Session 6 promoted rishi-6. Before that, rishi-5 was a functioning (if contended) leader — logs show `no action. I am (patroni-rishi-5), the leader with the lock` continuously through 09:42 on 07-21. The auth failure is a **consequence of the recovery**, not a pre-existing 2/3-quorum condition: when rishi-5 tried to rewind/re-basebackup against the newly-promoted rishi-6, rishi-6's `pg_hba.conf` has no non-SSL `host … postgres …` entry (only `local … trust` and `hostssl replication standby … md5`) and/or the superuser secret differs, so the connection is rejected. This is **superuser pg_hba/secret drift on rishi-6** that only surfaces when rishi-6 is leader (it had not led since 07-06).

---

## Answers to the other questions

- **Q4 — did /dev/shm=64MB (PR #457) contribute? NO.** `DiskFullError: could not resize shared memory segment` last fired **07-10** in Sentry (escalating 07-08 ×1 → 07-09 ×11 → 07-10 ×62), all *before* the 07-13 bridge mitigation. **Zero** shm errors in the incident window. `max_parallel_workers_per_gather: 0` is still present in DCS config. The mitigation held; PR #457 is not implicated.
- **Q5 — connection storm cause or symptom? Symptom.** Leader loss came first (09:44–09:45); `TooManyConnectionsError` followed as apps retried against a leaderless DB. No `too many clients` / `remaining connection slots` in any Patroni log. pg_stat_activity is now clean (46 idle / 0 active).
- **Q3 — host/network event?** The event was the coordinated kernel-upgrade reboot (not a Hetzner hardware incident — clean systemd shutdown in the journal, no OOM/dmesg anomalies on any node). The `Synchronous replication key updated by someone else` contention on 07-21 points to intermittent **etcd/overlay-DNS instability** as the proximate lock-loss trigger — the same overlay-DNS class flagged in the 2026-07-07 post-mortem.

---

## E) Recommendations (ranked by impact)

1. **Stagger the kernel auto-reboot window across the 3 Patroni nodes.** *(Highest — kills the root cause and the recurring pattern.)* Set a different `Automatic-Reboot-Time` per node in unattended-upgrades, or disable auto-reboot on DB nodes and do manual rolling reboots. All-3-in-one-minute has recurred ~monthly for 3 months and **will recur next Monday (~07-27)** unless changed.
2. **Add a Patroni leader-presence alert to the watchdog.** *(High — this is the missing page.)* Poll `:8008/cluster` (or DCS) and alert to Sentry when there is no leader / `cluster_unlocked: true` for > ~60s. This alone would have turned a silent 2h40m outage into an immediate page.
3. **Repair rishi-5 (active standing risk — cluster is 2/3 right now).** Fix the rishi-6 superuser pg_hba/secret drift and do a controlled `reinit` of rishi-5. **Ops action for Session 6, not this read-only session.**
4. **Document + tool the manual-failover recovery** so the sync-mode deadlock is a <5-min runbook, not an hours-long hunt. Keep the `synchronous_mode` safety guard (it correctly prevents data loss) but pair it with rec #2's alert.

**Should PR #457's redeploy window be prioritized now? NO.** #457 fixes the shm/DiskFull class, which did not recur here and is already bridged by the 07-13 `max_parallel_workers_per_gather=0` setting. It's worth landing eventually (to drop the bridge's query-plan penalty) but it would **not** have prevented this cascade. Prioritize #1 and #2.

---

## F) Standing risks (ticking, not part of this incident)

- **Cluster is 2/3 (rishi-5 down) → no fault tolerance.** A single failure on rishi-4 or rishi-6 now = another outage. Fix rishi-5 promptly (rec #3).
- **Next all-node Monday reboot (~07-27) will happen** unless the window is staggered (rec #1). High odds of a repeat.
- **Watchdog auto-heal for `yral-analytics_analytics` self-disabled until +1h** (logged `VERIFY FAILED … disabled until +1h` @ 07-21 10:23). Confirm analytics fully recovered.
- **Intermittent etcd / overlay-DNS instability** (`Synchronous replication key updated by someone else`) independent of reboots — same class as the 2026-07-07 post-mortem. Worth a dedicated etcd-health review.
