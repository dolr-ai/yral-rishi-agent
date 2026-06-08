# G — ETL backlog measurement

## TL;DR

**Estimated current chat-ai-not-in-v2 gap: ~20.5k messages + ~400 conversations** — the chat-ai-native growth since the re-bootstrap on 2026-06-04. Small enough that a **mini re-bootstrap at cutover moment** (proven path, ~30 min wall, ~2 min apply) is safer than re-enabling the 5-min S3-CSV ETL pre-α. Recommend: leave ETL OFF, do a mini re-bootstrap as part of the cutover playbook.

## Raw numbers (2026-06-08 06:05 UTC)

### chat-ai prod (read from rishi-1 replica, lag 0)

```
msgs created since 2026-05-30 00:00 UTC: 73,594
convs created since 2026-05-30 00:00 UTC:   1,447
latest msg created_at:                       2026-06-08 06:05:11 UTC
total msgs now:                          3,387,279
total convs now:                           286,731
```

### v2 prod

```
v2 total msgs now:                       3,436,630   (chat-ai + v2-native)
v2 total convs now:                        286,793
v2 latest msg created_at:                   2026-06-08 06:05:52 UTC
v2 msgs created since re-bootstrap moment: 32,711    (v2-native + bootstrap-completion)
v2 convs created since re-bootstrap moment:   404
```

### Cross-source diff

| | chat-ai | v2 | diff |
|---|---:|---:|---:|
| total messages | 3,387,279 | 3,436,630 | v2 +49,351 |
| total convs    | 286,731   | 286,793   | v2 +62 |
| latest message | 06:05:11  | 06:05:52  | both currently live |

v2 has MORE total rows than chat-ai overall (because of v2-native traffic on top of the bootstrap). The question is whether chat-ai has rows v2 is MISSING, not whether v2 is "behind."

## Estimating the gap

At the re-bootstrap snapshot (2026-06-04 ~11:00 UTC), chat-ai had **3,366,739** messages + **286,326** conversations. Today chat-ai has 3,387,279 + 286,731.

Growth on chat-ai since the re-bootstrap moment:
```
+20,540 messages    (3,387,279 − 3,366,739)
+405 conversations  (286,731 − 286,326)
```

That growth happened on chat-ai over the last 4 days. Some of those rows may also exist in v2 (if a user has accounts on both, or if some of those chat-ai-native rows were Option-A duplicate-skipped during the bootstrap). But **the vast majority of those 20.5k messages are chat-ai-only and represent the current ETL gap.**

For the wider 2026-05-30 → now window (since the ETL emergency disable):
- 73,594 chat-ai messages
- 1,447 chat-ai conversations
- ~53k of those were captured by the 2026-06-04 re-bootstrap; ~20.5k of those accumulated AFTER the bootstrap

## Re-enable ETL pre-α vs mini re-bootstrap at cutover?

### Option A — re-enable continuous ETL pre-α
- Drains the ~20.5k message gap in ~1h via 5-min ticks (~12 ticks × ~1700 msgs/tick)
- **Risk:** uses the same S3-CSV pipeline that produced the 8,932 orphan messages during the re-bootstrap. The chat-ai → S3 export side may still have the data-quality problems we patched around at bootstrap time
- **Risk:** restart contention — if the ETL fires while chat-ai is still serving live writes, we keep getting more orphans / more skips
- Adds the loop back into the cluster's background-load footprint right before cutover (more failure modes to monitor)

### Option B — mini re-bootstrap at cutover moment (RECOMMENDED)
- Same proven path as 2026-06-04: pg_dump from chat-ai leader → restore to sidecar → apply via the existing python script (`/tmp/rebootstrap_apply.py` — still works as-is)
- Wall clock: ~30 min total. Bulk is pg_dump (~10 min) + scp (~3 min) + sidecar+restore (~2 min). Apply itself is ~2 min for 20k messages.
- Definitive: snapshots AT the cutover moment after chat-ai stops taking writes → zero post-bootstrap gap
- Reuses the orphan-filter machinery we already validated; the 8,932 orphans count is now baseline so we know what to expect

### Why I lean Option B

1. **20.5k messages is well within "fast apply" territory.** The 2026-06-04 bootstrap applied 27,318 messages in 207s; another 20k is ~150s.
2. **chat-ai writes stop at cutover.** That's the cleanest moment for a snapshot — no race with new writes. If we pre-α re-enable ETL, we still need a final mini-bootstrap at cutover anyway to catch the last few minutes.
3. **The 5-min ETL is currently in unknown state.** It was disabled in an emergency 9 days ago; the S3 export side is similarly uncertain. Re-enabling without a fresh smoke test is real risk.
4. **The re-bootstrap script is sitting at `/tmp/rebootstrap_apply.py` ready to go.** Just need to re-mint a fresh pg_dump.

## Recommendation

**Cutover gate G: YELLOW with a clear plan.**

- Do NOT re-enable the 5-min S3-CSV ETL pre-α
- Track "mini re-bootstrap at cutover" as a step in the cutover playbook (~30 min including pg_dump + scp + apply)
- Use the same SSH carve-out + pg_dump + sidecar-pg16 + apply-script pattern from 2026-06-04
- Expected applied counts: ~20,500 messages + ~400 convs + ~0 ai_influencers (chat-ai bot creation is rare)

If the answer changes (e.g. Rishi decides cutover window is fixed-short and 30 min isn't available), the fallback is "accept the gap" — those 20.5k messages + 405 convs become permanent v2-doesn't-have-them. For an internal α cohort that's likely tolerable; for β it's not.

## What I did NOT do

- Did NOT enumerate exactly which rows are missing (would require an `id IN (chat-ai-ids) - id IN (v2-ids)` cross-DB query; expensive on 3.4M-row tables. The aggregate count is sufficient signal for a cutover decision.)
- Did NOT touch the ETL loop kill-switch state (still OFF per emergency disable)
- Did NOT take a pg_dump tonight (per the spec: snapshot must be taken AT cutover-time to be useful)
