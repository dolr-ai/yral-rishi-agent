"""Integration: migration 052's surface column against a REAL Postgres.

The property that actually protects us is a database default, not
application logic: every existing persona must land on 'mobile' without
being touched, so a missed backfill can never publish the mainstream
catalogue to amorae.ai. That is only provable against a real database.

Also pins the CHECK constraint, because the failure mode of a typo'd
surface ('Web') is silent — the row simply stops matching either product.
"""

import asyncio

import asyncpg
import pytest


def _run(dsn, fn):
    async def _go():
        conn = await asyncpg.connect(dsn)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_column_is_not_null_with_mobile_default(pg_dsn):
    async def _check(conn):
        return await conn.fetchrow(
            """
            SELECT is_nullable, column_default, data_type
            FROM information_schema.columns
            WHERE table_name = 'ai_influencers' AND column_name = 'surface'
            """
        )

    row = _run(pg_dsn, _check)
    assert row is not None, "migration 052 did not create surface"
    assert row["is_nullable"] == "NO"
    # The default is the safety mechanism — losing it means new rows land
    # with no surface and the NOT NULL just starts rejecting inserts.
    assert "mobile" in (row["column_default"] or "")


def test_existing_rows_default_to_mobile_without_backfill(db, pg_dsn):
    """Insert WITHOUT naming surface — exactly how every pre-migration row
    and every current INSERT in the codebase behaves."""

    async def _check(conn):
        await conn.execute(
            """
            INSERT INTO ai_influencers (id, name, display_name, system_instructions, is_active)
            VALUES ('surf-legacy', 'legacy_bot', 'Legacy Bot', 'x', 'active')
            """
        )
        return await conn.fetchval(
            "SELECT surface FROM ai_influencers WHERE id = 'surf-legacy'"
        )

    assert _run(pg_dsn, _check) == "mobile"


def test_check_constraint_rejects_an_invalid_surface(db, pg_dsn):
    async def _check(conn):
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO ai_influencers
                    (id, name, display_name, system_instructions, is_active, surface)
                VALUES ('surf-bad', 'bad_bot', 'Bad Bot', 'x', 'active', 'Web')
                """
            )
        return True

    assert _run(pg_dsn, _check)


def test_web_query_returns_web_and_both_but_never_mobile(db, pg_dsn):
    """The exact predicate the route runs for amorae-web."""

    async def _check(conn):
        await conn.execute(
            """
            INSERT INTO ai_influencers
                (id, name, display_name, system_instructions, is_active, surface)
            VALUES
                ('surf-m', 'm_bot', 'M', 'x', 'active', 'mobile'),
                ('surf-w', 'w_bot', 'W', 'x', 'active', 'web'),
                ('surf-b', 'b_bot', 'B', 'x', 'active', 'both')
            """
        )
        web = [
            r["id"]
            for r in await conn.fetch(
                """
                SELECT id FROM ai_influencers
                WHERE is_active != 'discontinued'
                  AND surface = ANY($1::text[])
                  AND id LIKE 'surf-%'
                ORDER BY id
                """,
                ["web", "both"],
            )
        ]
        unfiltered = [
            r["id"]
            for r in await conn.fetch(
                """
                SELECT id FROM ai_influencers
                WHERE is_active != 'discontinued'
                  AND ($1::text[] IS NULL OR surface = ANY($1::text[]))
                  AND id LIKE 'surf-%'
                ORDER BY id
                """,
                None,
            )
        ]
        return web, unfiltered

    web, unfiltered = _run(pg_dsn, _check)

    # Web sees only what opted in.
    assert web == ["surf-b", "surf-w"]
    assert "surf-m" not in web
    # NULL surfaces param is a genuine no-op — this is what keeps mobile's
    # current behaviour unchanged.
    assert unfiltered == ["surf-b", "surf-m", "surf-w"]
