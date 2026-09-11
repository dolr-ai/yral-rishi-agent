"""Integration: migration 055's liveness-scoped name uniqueness, real Postgres.

The guarantee here is a database one, not application logic, and that
distinction is the whole point of the migration. `create_influencer` checks
`get_by_name` first, but that check is not atomic and never was — the index is
what actually decides. Issue #513 proposed filtering the check alone; that
would have passed the check and then hit the table-wide UNIQUE on `name` as an
uncaught UniqueViolationError, turning a clean 409 into a 500.

So these assert the three outcomes that matter, against real DDL:
  - a DELETED persona's name can be taken again
  - a BANNED persona's name cannot (abuse handles stay locked)
  - two LIVE personas still cannot share a name
"""

import asyncio
import uuid
from datetime import datetime, timezone

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


async def _insert(conn, name, *, deleted=False, banned=False):
    """Insert a persona the way the app writes one.

    `deleted` mirrors soft_delete: discontinued AND deleted_at stamped.
    `banned` mirrors ban: discontinued with deleted_at left NULL. The
    difference between those two is the entire point of migration 055.
    """
    await conn.execute(
        """
        INSERT INTO ai_influencers
            (id, name, display_name, system_instructions, is_active, deleted_at)
        VALUES ($1, $2, $3, 'x', $4, $5)
        """,
        f"probe-{uuid.uuid4()}",
        name,
        "Deleted Bot" if deleted else "Probe",
        "discontinued" if (deleted or banned) else "active",
        datetime.now(timezone.utc) if deleted else None,
    )


def test_deleted_persona_frees_its_name(pg_dsn):
    """The bug in #513: a soft-deleted persona held its name forever."""
    name = f"probe-{uuid.uuid4().hex[:12]}"

    async def _check(conn):
        await _insert(conn, name, deleted=True)
        # A brand-new live persona must be able to claim the same handle.
        await _insert(conn, name)
        return await conn.fetchval(
            "SELECT count(*) FROM ai_influencers WHERE name = $1", name
        )

    assert _run(pg_dsn, _check) == 2


def test_banned_persona_keeps_its_name_locked(pg_dsn):
    """ban() must NOT free the handle — it writes is_active='discontinued'
    like soft_delete, but deliberately leaves deleted_at NULL. Filtering on
    is_active alone (as #513 suggested) would have freed these too."""
    name = f"probe-{uuid.uuid4().hex[:12]}"

    async def _check(conn):
        await _insert(conn, name, banned=True)
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await _insert(conn, name)

    _run(pg_dsn, _check)


def test_two_live_personas_still_cannot_share_a_name(pg_dsn):
    """The guarantee the partial index must not weaken."""
    name = f"probe-{uuid.uuid4().hex[:12]}"

    async def _check(conn):
        await _insert(conn, name)
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await _insert(conn, name)

    _run(pg_dsn, _check)


def test_index_is_partial_and_the_table_wide_constraint_is_gone(pg_dsn):
    """Pins the shape, so a later migration re-adding a full UNIQUE on name
    (which would silently restore the bug) fails loudly here."""

    async def _check(conn):
        idx = await conn.fetchval(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'ai_influencers'
              AND indexname = 'ai_influencers_name_live_key'
            """
        )
        old = await conn.fetchval(
            """
            SELECT count(*) FROM pg_constraint
            WHERE conrelid = 'ai_influencers'::regclass
              AND conname = 'ai_influencers_name_key'
            """
        )
        return idx, old

    indexdef, old_constraint = _run(pg_dsn, _check)
    assert indexdef is not None, "migration 055 did not create the partial index"
    assert "WHERE (deleted_at IS NULL)" in indexdef, indexdef
    assert old_constraint == 0, "the table-wide UNIQUE on name is back — #513 returns"


def test_restoring_a_deleted_persona_re_enters_name_uniqueness(pg_dsn):
    """unban() must clear deleted_at, not just is_active.

    Found reviewing this migration rather than from a report: ban() never sets
    deleted_at, so ban -> unban is unaffected — but unban is reachable for a
    SOFT-DELETED row, and restoring is_active while leaving deleted_at set
    would put a live persona outside the partial index. Its name would still
    read as free and a second live persona could take it.
    """
    name = f"probe-{uuid.uuid4().hex[:12]}"

    async def _check(conn):
        await _insert(conn, name, deleted=True)
        # The real unban statement.
        await conn.execute(
            """
            UPDATE ai_influencers
            SET is_active = 'active', deleted_at = NULL, updated_at = NOW()
            WHERE name = $1
            """,
            name,
        )
        # Back in the index: a second live persona must NOT be able to take it.
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await _insert(conn, name)

    _run(pg_dsn, _check)


def test_name_lookup_prefers_the_live_row_over_the_deleted_one(pg_dsn):
    """A name can now belong to one deleted row AND one live row, so
    get_by_id_or_name's `LIMIT 1` was a coin flip. Admin ban/unban must land
    on the live persona."""
    name = f"probe-{uuid.uuid4().hex[:12]}"

    async def _check(conn):
        await _insert(conn, name, deleted=True)
        await _insert(conn, name)
        return await conn.fetchrow(
            """
            SELECT is_active, deleted_at FROM ai_influencers
            WHERE id = $1 OR name = $1
            ORDER BY (deleted_at IS NULL) DESC, created_at DESC
            LIMIT 1
            """,
            name,
        )

    row = _run(pg_dsn, _check)
    assert row["deleted_at"] is None, "name lookup resolved to the DELETED row"
    assert row["is_active"] == "active"
