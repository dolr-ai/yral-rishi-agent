# DB Schema Ownership

> One Patroni cluster on rishi-4/5/6. 13+ schemas, one owner each. Cross-schema reads via VIEWS only; writes always through the owning service's API.

## The 13 schemas + ownership

| Schema | Owner service | Tables | Tier (per J1) |
|---|---|---|---|
| `conversation` | conversation-turn-orchestrator | conversations, messages, read_states, typing_state | HOT |
| `user_memory` | user-memory-service | semantic_facts, user_profiles, episodic_events, message_embeddings (pgvector) | WARM |
| `soul_file` | soul-file-library | global_layer_versions, archetype_layers, per_influencer_soul_files, active_layer_pointers | WARM |
| `influencer` | influencer-and-profile-directory | ai_influencers, llm_routing_rules, follower_counts | HOT |
| `human_profile` | influencer-and-profile-directory (same owner) | human_user_profiles, subscribe_states | WARM |
| `billing` | payments-and-creator-earnings | access_cache, paywall_counters, creator_earnings_rollups | HOT |
| `safety` | content-safety-and-moderation | moderation_events, crisis_flags, age_verifications, banned_words_per_locale | HOT |
| `skill` | skill-runtime | skill_registry, mcp_endpoints, skill_executions | COOL |
| `proactive` | proactive-message-scheduler | scheduled_sends, dormancy_flags, streaks, throttle_state | COOL |
| `media` | media-generation-and-vault | content_requests, content_vault, generation_jobs | COOL |
| `creator_studio` | creator-studio | soul_file_coach_sessions, bot_quality_scores, creator_analytics_rollups | COOL |
| `analytics` | events-and-analytics | events, cohort_rollups, kpi_snapshots | COOL |
| `admin` | shared (coordinator-managed) | feature_flags, experiments, advisor_recommendations | COOL |

Plus 1 staging-prefixed schema per service when running in staging environment (per F4): `staging_<schema>`.

## Ownership rules

```
   ┌──────────────────────────────────────────────────────────────┐
   │  ONLY THE OWNING SERVICE WRITES.                              │
   │  ─────────────────────────────                                │
   │  Each service has a Postgres ROLE that has WRITE permission   │
   │  on its OWN schema only. Other schemas: READ-only via views.  │
   │                                                                │
   │  Example: orchestrator service role can:                      │
   │    INSERT/UPDATE/DELETE on conversation.*                     │
   │    SELECT on user_memory.*  (via read-only view)              │
   │    SELECT on soul_file.*    (via read-only view)              │
   │    SELECT on influencer.*   (via read-only view)              │
   │    NO ACCESS to billing.*, safety.*, etc.                     │
   └──────────────────────────────────────────────────────────────┘
```

## Cross-schema READ patterns

When service A needs data owned by service B:

**Preferred:** API call to service B (HTTP, decoupled, can be cached).

**Acceptable:** Read-only view in service B's schema, granted to service A. Use only when:
- A and B always co-deploy (e.g., orchestrator + soul-file always together)
- The query pattern is stable (won't change frequently)
- Performance requires direct DB read (avoiding network hop)

**Forbidden:** Service A directly querying service B's tables. Always go through view.

## ID preservation rules (per A4)

- AI influencer IDs: PRESERVED from chat-ai during ETL Day 9
- Conversation IDs: PRESERVED
- Message IDs: PRESERVED
- User IDs: PRESERVED (already external — yral-auth-v2 owns these)

This means existing mobile deep-links keep working in v2 without translation.

## pgvector (user_memory.message_embeddings)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE user_memory.message_embeddings (
    message_id UUID PRIMARY KEY,
    embedding VECTOR(1536),
    indexed_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast nearest-neighbor search
CREATE INDEX ON user_memory.message_embeddings
USING hnsw (embedding vector_cosine_ops);
```

Cosine similarity (decided 2026-04-?? per Codex review of memory architecture).

## Migrations

- alembic per service (each service has its own `alembic/` folder)
- Migrations target ONLY the service's own schema
- Cross-schema migrations (rare) coordinated via coordinator + multi-PR
- Per H11: every migration PR runs against WAL-restored yesterday-prod snapshot

## Connection pooling

All services connect through `pgbouncer:5432` (per G3). Connection pool config per service:

| Tier | Pool size | Idle timeout |
|---|---|---|
| HOT (orchestrator, public-api) | 20 | 60s |
| WARM | 10 | 120s |
| COOL | 5 | 300s |

pgBouncer in front of Patroni handles primary discovery + read-replica routing.

## Schema bootstrap (Day 9 ETL)

Session 5's ETL script:
1. Connect as superuser
2. CREATE SCHEMA for each
3. CREATE ROLE for each service, GRANT on its schema only
4. Create read-only views for cross-schema patterns
5. Run alembic migrations per service
6. Import chat-ai data with ID preservation
7. Verify counts

After Day 9, each service spawn (Sessions 3, 4) just runs its own alembic migrations within its schema; never touches other schemas.

## What this enables

- **Strong isolation:** a buggy service can't corrupt another's data
- **Clear ownership:** when something's wrong with `messages`, you know to look at orchestrator
- **Easy auditing:** Postgres logs show which role wrote what
- **Migration safety:** a migration in service A can't accidentally drop service B's table
- **Schema-level backups:** if needed, restore one schema independently

## What this constrains

- New cross-cutting features (e.g., a "mention everyone" feature reading multiple schemas) need coordinator design — not just one session
- Refactors that move tables between services require the source schema to grant temporary access during migration
- Some queries that would be one-table joins in a single-DB system become two-API calls in our pattern; we accept this for isolation
