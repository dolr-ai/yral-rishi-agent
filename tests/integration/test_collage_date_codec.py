"""Integration: the collage_date asyncpg DATE codec — the bug the mocks hid.

`message_repo.create` passes `collage_date` straight to asyncpg's DATE
codec. A real `datetime.date` persists; a raw ISO string raises DataError
(the 2026-07-14 Sarvesh 500). The old mocked-pool tests accepted BOTH, so
they never saw this — which is exactly why the route layer needs the
`_parse_collage_date` guard (app/routes/chat.py:531).

This file is the acceptance proof for Wave 1: `test_..._string_rejected...`
reproduces the real bug against a real Postgres. If someone deleted the
route guard, that string would reach this codec and 500 in prod — and only
a real-DB test like this can see it coming.
"""

import asyncio
from datetime import date

import asyncpg
import pytest

CONV_ID = "conv-codec-test"
USER_ID = "user-codec-test"


async def _seed_conversation(pool):
    await pool.execute(
        "INSERT INTO conversations (id, user_id) VALUES ($1, $2)",
        CONV_ID,
        USER_ID,
    )


def test_collage_date_accepts_real_date(db):
    """The happy path the guard produces: a datetime.date persists and reads
    back as the same date through the real codec."""
    from repositories import message_repo

    async def _run():
        pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=2)
        try:
            await _seed_conversation(pool)
            return await message_repo.create(
                pool,
                conversation_id=CONV_ID,
                role="assistant",
                content=None,
                message_type="collage",
                collage_date=date(2026, 7, 14),
            )
        finally:
            await pool.close()

    row = asyncio.run(_run())
    assert row["collage_date"] == date(2026, 7, 14)


def test_collage_date_string_rejected_by_real_codec(db):
    """The exact production bug reproduced: a raw ISO string reaches asyncpg's
    DATE codec and is rejected. A mocked pool accepted this silently; a real
    Postgres does not. This is the regression guard for the route-layer fix."""
    from repositories import message_repo

    async def _run():
        pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=2)
        try:
            await _seed_conversation(pool)
            await message_repo.create(
                pool,
                conversation_id=CONV_ID,
                role="assistant",
                content=None,
                message_type="collage",
                collage_date="2026-07-14",  # the bug: a str, not a date
            )
        finally:
            await pool.close()

    with pytest.raises(asyncpg.DataError):
        asyncio.run(_run())
