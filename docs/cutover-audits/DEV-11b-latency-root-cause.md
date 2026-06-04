# DEV-11b — Latency root cause (inbox-list 2× slower)

## TL;DR

**Root cause: stale planner stats post-failover.** `pg_stat_user_tables` shows `conversations.n_live_tup=2` (reality: 286k), `messages.n_live_tup=1929` (reality: 3.4M), and `last_analyze` + `last_autoanalyze` are BOTH NULL across all three target tables. The stats appear to have reset during yesterday's WAL-G rolling restart (failover from rishi-6 → rishi-5 leader) and autovacuum hasn't fired since. **`ANALYZE conversations; ANALYZE messages; ANALYZE ai_influencers;` will materially fix the regression.** The query also has structural inefficiencies (GROUP-BY-before-LIMIT, double scan of ai_influencers) that ANALYZE won't help with — those are a separate β-class optimization PR.

## Evidence

### `pg_stat_user_tables` on rishi-5 leader

```
    relname     | n_live_tup | n_dead_tup | last_analyze | last_autoanalyze | last_vacuum | last_autovacuum
----------------+------------+------------+--------------+------------------+-------------+-----------------
 ai_influencers |          0 |          0 |              |                  |             |
 conversations  |          2 |       1251 |              |                  |             |
 messages       |       1929 |          1 |              |                  |             |
```

All four `last_*` columns NULL → **never analyzed since stats-counter reset.** The reset almost certainly happened at the rishi-5 promotion during yesterday's rolling restart (TL=25 → TL=26). Patroni's promotion doesn't preserve `pg_stat_*` collector data; it starts fresh.

### EXPLAIN ANALYZE of the inbox-list query (LIMIT 20 OFFSET 0)

Test user: heavy user with 568 conversations.

Plan summary:
- **Execution Time: 578.389 ms** (matches DEV-11's ~900ms p50 minus network)
- Outermost `Nested Loop Left Join` between conversations and messages: **estimated 12 rows, actual 4789**
- Inner `Hash Left Join` (c JOIN i): **estimated 1 row, actual 567** — **567× off**
- `Bitmap Heap Scan on messages m` (per outer loop): estimated 98 rows, actual 8 per loop × 567 loops = 4789 rows total
- Two `Seq Scan on ai_influencers`: once for the `NOT IN (SELECT id FROM ai_influencers)` filter (608 buffers), once for the JOIN (608 buffers) — 3941 rows × 2 = 7882 scans + buffer churn
- `SubPlan 1` for unread_count: runs 20 times (once per LIMIT 20), 0.023ms each — fast, not the bottleneck

### Why ANALYZE will fix the bulk of this

The catastrophic 567× row-estimate underestimation on the Hash Left Join is **directly caused** by `conversations.n_live_tup=2`. With correct stats:
- Planner sees ~286k conversations, with the user_id-index narrowing to ~600 for this user
- Hash Left Join with `i` would estimate close to actual (567 vs 567)
- Nested Loop downstream gets the right loop count → may switch to Hash Aggregate for the COUNT(m.id) GROUP BY
- Faster plan, fewer disk reads

Expected post-ANALYZE inbox-list p50: ~300-400ms (close to chat-ai's 427ms parity, possibly faster).

## ANALYZE is safe and cheap

- `ANALYZE` requires only **ACCESS SHARE** lock — does NOT block readers or writers
- Writes to `pg_statistic` (a tiny system table) — completes in seconds for table sizes here
- Even though it modifies pg_statistic (technically a write), it's the OPPOSITE of disruptive: it's restoring planner correctness
- Standard maintenance operation; runs automatically when autovacuum fires (just hasn't fired here yet)

Estimated wall time: 10-30s per table. Total ~1 minute.

## Structural inefficiencies ANALYZE won't fix (β-class follow-up)

Even with perfect stats, the query has two issues:

### 1. GROUP BY happens BEFORE LIMIT

The query does:
```sql
SELECT ..., COUNT(m.id) AS message_count, (correlated unread subquery) AS unread_count
FROM conversations c LEFT JOIN messages m ON c.id = m.conversation_id
WHERE <user filter>
GROUP BY c.id, i.id
ORDER BY c.updated_at DESC
LIMIT 20
```

This computes `COUNT(m.id)` for ALL the user's conversations (567 in our test), THEN orders + LIMIT 20. It's doing 547 wasted COUNTs.

Rewrite: move the COUNT into a correlated subquery in the SELECT, identical shape to `unread_count`. Then the LIMIT 20 fires BEFORE the count, and only the 20 returned rows compute their counts. Expected: 4-5× speedup on inbox-list with deep conversation lists.

### 2. Double scan of ai_influencers

The `NOT IN (SELECT id FROM ai_influencers)` filter forces one `Seq Scan on ai_influencers` to build the hash; the `LEFT JOIN ai_influencers i` does another. 7882 sequential row reads for the same table data.

Two fixes:
- Replace `NOT IN` with `NOT EXISTS (SELECT 1 FROM ai_influencers WHERE id = c.user_id)` so the planner can prove the subquery result is the same as the JOIN's hash side
- OR (heavier change) split the WHERE into per-`conversation_type` UNION ALL — the ai_chat branch doesn't need the NOT IN filter (every ai_chat conv has a non-bot user_id by construction)

Either is a small PR. Not blocking α.

### 3. Other queries to check

Same root cause (stale stats) likely affects:
- `messages.get_recent_for_context(conv_id, 11)` — used on every chat-send to pull 10 history messages for the LLM. Likely fast (single conversation_id index lookup) but worth checking.
- `messages.count_unread(...)` — similar shape to the inbox-list correlated subquery; same partial index covers it.
- `bot_quality_scores.latest_for_bot(bot_id)` — fine; small table.

**Recommendation:** after ANALYZE conversations/messages/ai_influencers, also `ANALYZE bot_quality_scores; ANALYZE coach_conversations; ANALYZE coach_messages; ANALYZE user_memories; ANALYZE user_skill_state;`. Cheap; rules out other Phase-23/Phase-25 tables having the same stale-stats problem after the same failover.

## Will ANALYZE alone close the cutover-readiness gap?

**Yes — for inbox-list specifically.** Expected: p50 drops from 904ms → ~300-400ms. Likely beats chat-ai's 427ms; clears the DEV-11 red flag.

For chat-send: ANALYZE does NOT change the LLM round-trip time (that's the dominant ~2.5s). 50% target stays unachievable without provider change.

For inf-list: light query, minimal benefit from ANALYZE; should still be in the 300-400ms range it's at now.

## Recommendation

**Authorize ANALYZE before the morning go/no-go meeting.** Then re-run the latency comparison N=100. Expected result: inbox-list back at or below chat-ai parity, clearing the DEV-11 red.

Optional follow-up PR (β): rewrite `list_by_user` to push COUNT into a per-row subquery + replace `NOT IN` with `NOT EXISTS`. Estimated 4-5× additional inbox-list speedup on heavy users.

## Reproducibility

```bash
# Direct EXPLAIN ANALYZE — saved at /tmp/explain_inbox.sql, runs in ~600ms
docker run --rm --network yral-v2-data-plane -v /tmp/explain_inbox.sql:/q.sql \
  pgvector/pgvector:pg15 psql "$DBURL" -f /q.sql
```
