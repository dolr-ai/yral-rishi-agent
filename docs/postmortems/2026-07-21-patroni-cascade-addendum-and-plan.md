# Patroni cascade — Session 6 addendum + action plan (2026-07-23)

Companion to [`2026-07-21-patroni-cascade.md`](./2026-07-21-patroni-cascade.md).
That write-up (from the read-only diagnostic session) is accurate on the
**timeline, the outage window (09:45→12:27 UTC), and the missing-page gap**.
This addendum corrects the **root-cause mechanism** and current cluster state
based on read-only SSH verification done 2026-07-23, and lays out the plan.

---

## 1. Correction: the reboot is NOT unattended-upgrades

The post-mortem's headline recommendation (#1) was *"stagger the
unattended-upgrades reboot window / disable auto-reboot."* **That fix would do
nothing** — verified on all three nodes:

| Check (read-only, 2026-07-23) | rishi-4 | rishi-5 | rishi-6 |
|---|---|---|---|
| `apt-config dump \| grep Automatic-Reboot` | unset → **false** | unset → **false** | unset → **false** |
| Reboot Jul 20 | **08:48** | **08:48** | **08:48** |
| Kernel now | 6.8.0-136 | 6.8.0-136 | 6.8.0-136 |

Unattended-upgrades **auto-reboot is already disabled** on every node, and there
is no reboot cron/timer, no `kured`, no reboot systemd unit. So u-u is not the
initiator.

**The tell: all three independent VMs reboot in the same minute, on lockstep
kernels, every ~monthly Monday morning CEST** (May 18, Jun 1, Jun 8, Jul 6, Jul
20). Independent per-node apt timers cannot align to the same minute — a
*shared external trigger* can. This points to **Hetzner host/hypervisor
maintenance rebooting the VMs**, not anything inside the OS. (The kernel climbing
"each time" is incidental: apt stages the new kernel daily; the external reboot
just activates whatever is pending.)

**Consequence for the fix:** you cannot "stagger" a reboot you don't initiate.
The plan below replaces recommendation #1 accordingly.

## 2. Correction: the cluster is fragile *right now*

Live `patronictl list` / Patroni REST, 2026-07-23:

| Member | Role | State | TL | Lag |
|---|---|---|---|---|
| patroni-rishi-4 | **Leader** | running | 49 | — |
| patroni-rishi-6 | Replica | running | 48 | **~1.8 GB behind** |
| patroni-rishi-5 | Replica | **starting** | ? | unknown |

So it is effectively **1 healthy node + 2 unhealthy replicas → zero real fault
tolerance today.** rishi-5's postgres log shows `the database system is starting
up` / `Still starting up as a standby` / `following a leader (patroni-rishi-4)`
— it is replaying WAL against rishi-4, **not** auth-failing as it was during the
incident. It may catch up on its own or be stuck; §3 covers both.

If leader rishi-4 reboots at the next maintenance window with the cluster in this
shape, sync-mode's safety guard will again refuse to promote a lagging/starting
node → **repeat of the 07-20 leaderless outage.** This is the real urgency.

---

## 3. Action plan (revised, ranked)

### P0a — Leader-presence alert · **DONE, in review** → PR #463
The missing page. Watchdog now reads Patroni REST `/cluster` and alerts Sentry
when the cluster is leaderless > 90s. Awaiting review + approval; **not yet
deployed** (deploy process). This is the single highest-leverage item because it
turns any future leaderless event from a silent multi-hour outage into an
immediate page.

### P0b — Identify + control the real reboot trigger · **needs Rishi (Hetzner account)**
Because the trigger is external, the investigation is *outside* the VM:
1. Check the **Hetzner Cloud console → each server → maintenance/notifications**,
   and the account email, for scheduled-maintenance notices around the Monday
   dates. Confirm these are host reboots.
2. If Hetzner maintenance: check whether the window is schedulable, and whether
   the three VMs sit on infrastructure that gets rebooted together (co-located →
   simultaneous). If so, **spreading the DB VMs across different Hetzner
   host-groups/locations** would break the "all-3-same-minute" correlation — the
   actual equivalent of "staggering."
3. If it turns out to be in-VM after all (unlikely given the evidence), the
   remaining suspects are `needrestart`, kernel livepatch, or a drop-in I didn't
   surface — grep `/etc/needrestart/` and `journalctl -b -1` for the initiator.

### P0c — Make the cluster *survive* a simultaneous reboot · **durable fix**
Since we may not control the trigger, the cluster must ride out all-3-nodes-down
gracefully. This is gated on P1 (healthy replicas) + a review of the sync-mode
recovery path so that a clean simultaneous reboot re-elects a leader without a
manual failover. Pair with the runbook (P2).

### P1 — Repair rishi-5 + rishi-6 lag → real 3/3 · **plan below, needs Rishi go**
Get back to genuine fault tolerance. **Snapshot-first, one node at a time, NOT
executed by this session** — step-by-step in §4.

### P2 — Resilience follow-ups
- **<5-min manual-failover runbook** to pair with the (correct) sync-mode guard,
  so the next leaderless event is a 5-minute fix, not a 2h40m one.
- **Confirm yral-analytics fully recovered** — watchdog self-disabled its
  auto-heal for `yral-analytics_analytics` "until +1h" at 10:23 on 07-21.
- **etcd / overlay-DNS instability** — recurring class (also in the 2026-07-07
  post-mortem); deserves its own look.

### Explicitly NOT now — PR #457 (shm_size 2g)
Per the post-mortem: the `DiskFullError: could not resize shared memory` class
last fired 07-10, is already bridged (`max_parallel_workers_per_gather=0`), and
did **not** contribute to this cascade. Do **not** let #457 jump ahead of P0/P1.

---

## 4. rishi-5 / rishi-6 repair — step-by-step (snapshot-first, do not auto-run)

> Rule 9: take a `pg_dump` snapshot before any change. One node at a time. Run in
> a low-traffic window. Never reinit the node that is the *sole* sync standby.

0. **Pre-flight.** Confirm leader is healthy (`patronictl list` → rishi-4 Leader,
   running). Take a `pg_dump` snapshot from the leader → S3 (or WAL-G base
   backup) and verify it completed.
1. **Give rishi-5 a chance to self-heal.** It is already replaying WAL as a
   standby. Watch `docker logs` on the rishi-5 patroni container for LSN
   progress and `database system is ready to accept read only connections`. If
   it reaches `running` on its own, **no reinit needed** — go to step 5.
2. **If rishi-5 is stuck**, capture the specific blocker from postgres +
   patroni logs (pg_rewind failure? missing WAL segment? replication slot?
   auth?). Record it before acting.
3. **Verify secret consistency** across the three Patroni containers — the
   incident's auth failure came from superuser/standby secret drift on rishi-6.
   Confirm the Swarm secret / env the containers use is aligned on all three.
4. **Controlled reinit of rishi-5** from a manager:
   `docker exec <patroni-rishi-6> patronictl reinit yral-v2-postgres patroni-rishi-5`
   — wipes rishi-5's data dir and rebasebackups from the leader (~4 min, per the
   TL=24 reinit on 2026-06-04). Confirm the target is rishi-5, not the leader.
5. **rishi-6 lag (~1.8 GB).** Likely catches up once rishi-5 is healthy and load
   settles; if it stays stuck, same reinit path (step 4, target rishi-6) — but
   only after rishi-5 is a healthy sync standby, so the leader always has one.
6. **Verify:** `patronictl list` shows all three `running`, aligned timeline,
   lag → 0, and a designated sync standby exists.

Ops execution is a Rishi-authorized action, not this session's to run.
