"""Integration: migration 051's target_markets column against a REAL Postgres.

Spec: docs/us-market-launch-spec-2026-08-08.md (Track B, PR1).

The source-level tests in tests/test_market_config_and_migration_051.py can
only assert that the SQL *says* the right thing. These assert that Postgres
*did* the right thing — the column is a real TEXT[], the GIN index exists
and answers the containment predicate PR2 will use, and NULL genuinely
behaves as "global".

That last one is the load-bearing property of the whole launch: every one
of the 4,081 existing rows must stay visible everywhere with no backfill.
If `target_markets @> ARRAY['US']` ever matched a NULL row — or if the
inverse "is this row global?" test misread NULL — the US feed would leak
the Indian catalogue, or the global feed would empty out.
"""

import asyncio

import asyncpg


def _run(dsn, fn):
    async def _go():
        conn = await asyncpg.connect(dsn)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_column_exists_as_text_array(pg_dsn):
    """TEXT[] specifically — a plain TEXT column would silently accept the
    writes and then break the `@>` containment predicate PR2 depends on."""

    async def _check(conn):
        return await conn.fetchrow(
            """
            SELECT data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'ai_influencers' AND column_name = 'target_markets'
            """
        )

    row = _run(pg_dsn, _check)
    assert row is not None, "migration 051 did not create target_markets"
    assert row["data_type"] == "ARRAY"
    assert row["udt_name"] == "_text"
    # Nullable is the encoding for "global" — NOT NULL would break the
    # no-backfill guarantee.
    assert row["is_nullable"] == "YES"


def test_gin_index_exists(pg_dsn):
    async def _check(conn):
        return await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            "idx_ai_influencers_target_markets",
        )

    indexdef = _run(pg_dsn, _check)
    assert indexdef is not None, "migration 051 did not create the GIN index"
    assert "gin" in indexdef.lower()
    assert "target_markets" in indexdef


def test_null_means_global_and_containment_selects_only_tagged_rows(db, pg_dsn):
    """The exact predicate PR2's feed query will run.

    A NULL-market row (every existing persona) must NOT match `@> ARRAY['US']`,
    and must still be selectable as global. A US-tagged row must match US and
    not IN. A multi-market row must match each of its markets — that's the
    reason the column is an array rather than a single country string."""

    async def _check(conn):
        await conn.execute(
            """
            INSERT INTO ai_influencers
                (id, name, display_name, system_instructions, is_active, target_markets)
            VALUES
                ('mkt-global', 'global_bot', 'Global Bot', 'x', 'active', NULL),
                ('mkt-empty',  'empty_bot',  'Empty Bot',  'x', 'active', '{}'),
                ('mkt-us',     'us_bot',     'US Bot',     'x', 'active', ARRAY['US']),
                ('mkt-multi',  'multi_bot',  'Multi Bot',  'x', 'active', ARRAY['US','CA','GB'])
            """
        )
        exclusive_us = [
            r["id"]
            for r in await conn.fetch(
                """
                SELECT id FROM ai_influencers
                WHERE is_active = 'active'
                  AND target_markets @> ARRAY[$1]::text[]
                  AND id LIKE 'mkt-%'
                ORDER BY id
                """,
                "US",
            )
        ]
        exclusive_in = [
            r["id"]
            for r in await conn.fetch(
                """
                SELECT id FROM ai_influencers
                WHERE is_active = 'active'
                  AND target_markets @> ARRAY[$1]::text[]
                  AND id LIKE 'mkt-%'
                """,
                "IN",
            )
        ]
        globals_ = [
            r["id"]
            for r in await conn.fetch(
                """
                SELECT id FROM ai_influencers
                WHERE is_active = 'active'
                  AND (target_markets IS NULL OR cardinality(target_markets) = 0)
                  AND id LIKE 'mkt-%'
                ORDER BY id
                """
            )
        ]
        return exclusive_us, exclusive_in, globals_

    us, in_, globals_ = _run(pg_dsn, _check)

    # US market sees exactly the tagged personas — never the untagged catalogue.
    assert us == ["mkt-multi", "mkt-us"]
    # A market with nothing tagged for it matches nothing (PR2 must therefore
    # only ever consult this branch for countries in MARKET_EXCLUSIVE_COUNTRIES).
    assert in_ == []
    # NULL and '{}' both read as global — the no-backfill guarantee.
    assert globals_ == ["mkt-empty", "mkt-global"]
