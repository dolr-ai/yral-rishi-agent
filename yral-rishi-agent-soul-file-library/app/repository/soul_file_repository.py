# ---------------------------------------------------------------------------
# soul_file_repository.py — asyncpg-based data-access layer for the
# `soul_file_layers` table.
#
# ⭐ START HERE: this module is the ONLY place in the codebase that
# issues SQL against `soul_file_layers`. Everything else (composer, HTTP
# route) reaches the table through these functions. Centralising the SQL
# means a future schema bump touches ONE file.
#
# WHY ASYNCPG DIRECTLY + NOT SQLAlchemy
# Per the Day-4 directive verbatim: "no SQLAlchemy ORM — direct asyncpg
# + Pydantic models keeps the dep tree thin per A2.1." We use asyncpg's
# native `pool.fetchrow(...)` / `pool.fetch(...)` API + parse each Record
# into a `SoulFileLayer` Pydantic model at the boundary.
#
# WHY NO `create_new_version` / `retire_current` HTTP EXPOSURE TODAY
# Per the Day-4 directive verbatim: "No write methods exposed at HTTP
# today; repository has create_new_version() + retire_current() for
# tests + future Prompt-Coach wiring." The write functions are public
# Python callables so tests can exercise the partial-unique index and
# version-bump invariants, but no FastAPI route wires them. The future
# Prompt-Coach service (Day-5+) will add the route + auth + audit log.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Final

import asyncpg

from app.db import get_pool
from app.models.soul_file import SoulFileLayer


# Layer-number constants — used as named ints so callsites read as English
# (`LAYER_GLOBAL` vs `1`) per B1.
LAYER_GLOBAL: Final[int] = 1
LAYER_ARCHETYPE: Final[int] = 2
LAYER_PER_INFLUENCER: Final[int] = 3
LAYER_PER_USER_SEGMENT: Final[int] = 4


# ===========================================================================
# Read methods — used by the composer hot path
# ===========================================================================


async def get_current(layer: int, scope_key: str) -> SoulFileLayer | None:
    """Return the CURRENT row at `(layer, scope_key)`, or None if missing.

    WHAT: SELECT against `soul_file_layers` filtered to the row with
          `is_current=TRUE` for the given slot. The partial unique
          index in the migration enforces at most one such row.
    WHEN: called four times per composer invocation — once per layer.
    WHY:  composer hot path; needs to be O(1) on slot count. The
          partial unique index on `(layer, scope_key) WHERE
          is_current = TRUE` makes this an index-only lookup.

    Returns:
        The SoulFileLayer model if a current row exists; None if not.
        `None` is the signal the composer turns into a 404 for L3
        (unknown influencer) or a clearer error for L1/L2/L4 (data
        integrity issue — the migration's seed covers all of those).
    """
    pool = get_pool()

    record = await pool.fetchrow(
        """
        SELECT id, layer, scope_key, archetype, body, version, is_current,
               created_at, created_by
        FROM soul_file_layers
        WHERE layer = $1 AND scope_key = $2 AND is_current = TRUE
        """,
        layer,
        scope_key,
    )

    if record is None:
        return None

    return _record_to_model(record)


async def list_versions(layer: int, scope_key: str) -> list[SoulFileLayer]:
    """Return every version (current + historic) for `(layer, scope_key)`.

    WHAT: SELECT ordered by `version DESC` — most-recent first.
    WHEN: called by the RUNBOOK rollback flow (Prompt-Coach UI in
          Day-5+; today only `tests/test_repository.py` calls it).
    WHY:  rollback path — operator inspects history, picks a prior
          version, flips `is_current` flags via `create_new_version`.

    Returns:
        Possibly-empty list of SoulFileLayer models.
    """
    pool = get_pool()

    records = await pool.fetch(
        """
        SELECT id, layer, scope_key, archetype, body, version, is_current,
               created_at, created_by
        FROM soul_file_layers
        WHERE layer = $1 AND scope_key = $2
        ORDER BY version DESC
        """,
        layer,
        scope_key,
    )

    return [_record_to_model(r) for r in records]


# ===========================================================================
# Write methods — exposed for tests + future Prompt-Coach wiring; NOT
# wired to an HTTP route today.
# ===========================================================================


async def create_new_version(
    layer: int,
    scope_key: str,
    body: str,
    archetype: str | None = None,
    created_by: str | None = None,
) -> SoulFileLayer:
    """Insert a new row at `(layer, scope_key)` + flip prior current to false.

    WHAT: runs `UPDATE ... SET is_current=FALSE WHERE ... AND is_current=TRUE`
          then `INSERT ... VALUES (... version=prior+1, is_current=TRUE ...)`
          inside one transaction.
    WHEN: tests call this to exercise the partial-unique-index invariant
          + the version-bump path; future Prompt-Coach service will call
          it from an auth'd HTTP route.
    WHY:  one transactional retire-then-insert keeps the "exactly one
          current per slot" rule unviolated even under concurrent writes
          (the partial unique index would reject a second concurrent
          insert anyway, but the explicit retire makes the invariant
          intentional rather than incidental).

    Returns:
        The freshly-inserted SoulFileLayer (now `is_current=TRUE`).
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Retire the prior current row, if any. NO-OP when the slot
            # is empty (first-ever insert for this `(layer, scope_key)`).
            await conn.execute(
                """
                UPDATE soul_file_layers
                SET is_current = FALSE
                WHERE layer = $1 AND scope_key = $2 AND is_current = TRUE
                """,
                layer,
                scope_key,
            )

            # Compute next version. COALESCE handles the "first ever"
            # case (no prior rows → MAX is NULL → use 0 → +1 = 1).
            next_version_row = await conn.fetchrow(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM soul_file_layers
                WHERE layer = $1 AND scope_key = $2
                """,
                layer,
                scope_key,
            )
            next_version = int(next_version_row["next_version"])

            # Insert the new current row. RETURNING gives us back every
            # column without a second SELECT.
            new_record = await conn.fetchrow(
                """
                INSERT INTO soul_file_layers
                    (layer, scope_key, archetype, body, version,
                     is_current, created_by)
                VALUES ($1, $2, $3, $4, $5, TRUE, $6)
                RETURNING id, layer, scope_key, archetype, body, version,
                          is_current, created_at, created_by
                """,
                layer,
                scope_key,
                archetype,
                body,
                next_version,
                created_by,
            )

    return _record_to_model(new_record)


async def retire_current(layer: int, scope_key: str) -> bool:
    """Flip the current row's `is_current` to FALSE; no replacement.

    WHAT: UPDATE setting `is_current=FALSE` for whatever's currently
          flagged at `(layer, scope_key)`.
    WHEN: called by tests to assert partial-unique-index behaviour;
          NOT called by the runtime path today.
    WHY:  exposes a way to leave a slot intentionally empty (e.g.
          retire a deprecated archetype L2 row + don't replace it).
          Composer will return None for that slot on the next read,
          which the HTTP layer turns into a 404 / clear error.

    Returns:
        True if a row was retired; False if the slot was already empty.
    """
    pool = get_pool()

    result = await pool.execute(
        """
        UPDATE soul_file_layers
        SET is_current = FALSE
        WHERE layer = $1 AND scope_key = $2 AND is_current = TRUE
        """,
        layer,
        scope_key,
    )

    # asyncpg returns the row count as the last token of the status
    # string (`"UPDATE 1"` / `"UPDATE 0"`).
    return result.endswith(" 1")


# ===========================================================================
# Internal helper
# ===========================================================================


def _record_to_model(record: asyncpg.Record) -> SoulFileLayer:
    """Convert an asyncpg Record into a SoulFileLayer Pydantic model.

    WHAT: maps every column from the Record into the matching model
          field; converts UUID → str.
    WHEN: called from every public function in this module after a
          SELECT / INSERT RETURNING.
    WHY:  one mapping point means a future column add (e.g. an
          `updated_at` timestamp) needs ONE edit here, not N edits
          across every read path.
    """
    return SoulFileLayer(
        id=str(record["id"]),
        layer=int(record["layer"]),
        scope_key=record["scope_key"],
        archetype=record["archetype"],
        body=record["body"],
        version=int(record["version"]),
        is_current=bool(record["is_current"]),
        created_at=record["created_at"],
        created_by=record["created_by"],
    )


# ===========================================================================
# RELATED FILES:
#   __init__.py                     — package marker
#   ../db.py                        — `get_pool()` accessor this module uses
#   ../models/soul_file.py          — SoulFileLayer model returned here
#   ../composer/four_layer_composer.py
#                                  — primary consumer (4 get_current() calls
#                                    per turn)
#   ../api/composed_prompt_routes.py
#                                  — also calls get_current() to detect L3
#                                    misses + emit 404 cleanly
#   ../migrations/versions/001_initial_schema_and_seed.py
#                                  — table this module's SQL targets
#   ../../tests/test_repository.py  — CRUD + partial-unique tests
# ===========================================================================
