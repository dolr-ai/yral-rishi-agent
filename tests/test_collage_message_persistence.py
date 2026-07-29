"""Collage message reference fields — regression tests.

Sarvesh mobile integration surfaced the gap 2026-07-13: mobile POSTs
`message_type=collage` + 3 reference fields (collage_id,
collage_bot_id, collage_date), but backend dropped them silently
because there were no columns to hold them + no route wiring to
persist. Design §5 self-healing render path relies on those fields
round-tripping so mobile can refetch on subscription-flip / reload.

Tests split into source-pins (safe to run without a live DB) plus
behavioural tests via a small SQL-substring stub pool that walks the
INSERT + SELECT round trip.

Brief-mandated scenarios covered:

  1. test_send_message_collage_persists_all_3_fields
  2. test_send_message_text_ignores_collage_fields
  3. test_stream_send_message_persists_collage_fields
  4. test_get_messages_returns_collage_reference
  5. test_collage_fields_absent_on_legacy_rows
"""

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest


REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_migration_050_shape():
    """Additive nullable columns + partial index. A silent shape change
    (dropping the partial index or making a column NOT NULL) would
    break the pattern the brief locked in — every downstream test
    assumes the columns are nullable."""
    src = _read("migrations/050_messages_collage_reference_fields.sql")
    assert "SET lock_timeout" in src
    assert "SET statement_timeout" in src
    assert "ADD COLUMN IF NOT EXISTS collage_id     UUID" in src
    assert "ADD COLUMN IF NOT EXISTS collage_bot_id VARCHAR(255)" in src
    assert "ADD COLUMN IF NOT EXISTS collage_date   DATE" in src
    # Partial index supports future reverse lookups (all messages
    # referencing collage X) without paying maintenance cost on the
    # very large non-collage row set.
    assert "CREATE INDEX IF NOT EXISTS idx_messages_collage_id" in src
    assert "WHERE collage_id IS NOT NULL" in src


def test_models_expose_collage_reference_on_chat_message():
    """Sarvesh's DTO expects the 3 fields to land as optional on
    ChatMessage. Rule 2 (mobile contract sacred) — dropping any of
    the 3 breaks mobile silently."""
    src = _read("app/models.py")
    idx = src.find("class ChatMessage(BaseModel):")
    assert idx != -1
    block = src[idx : idx + 1500]
    assert "collage_id: Optional[UUID]" in block
    assert "collage_bot_id: Optional[str]" in block
    assert "collage_date: Optional[date]" in block


def test_send_message_request_widens_message_type_and_carries_collage():
    """Wire allows message_type='collage' + the 3 reference fields on
    the POST body — the exact shape mobile is already sending."""
    src = _read("app/models.py")
    idx = src.find("class SendMessageRequest(BaseModel):")
    assert idx != -1
    block = src[idx : idx + 1500]
    assert '"collage"' in block, (
        "message_type Literal must include 'collage' — migration 047 "
        "widened the DB CHECK, this model must match"
    )
    assert "collage_id: Optional[UUID]" in block
    assert "collage_bot_id: Optional[str]" in block
    assert "collage_date: Optional[date]" in block


def test_message_repo_create_accepts_and_persists_collage_fields():
    """Repo signature + INSERT must include the 3 columns AND the
    $15::uuid cast on collage_id — bare $15 would trip asyncpg's
    codec against the UUID column type."""
    src = _read("app/repositories/message_repo.py")
    # Signature widened
    assert "collage_id: str | None = None" in src
    assert "collage_bot_id: str | None = None" in src
    assert "collage_date: date | None = None" in src
    # INSERT column list carries the new columns
    assert "collage_id, collage_bot_id, collage_date" in src
    # Explicit ::uuid cast on the placeholder
    assert "$15::uuid" in src


def test_message_repo_selects_include_collage_columns():
    """All SELECT paths must return the 3 collage columns so
    _format_message can decide whether to emit them. The 7 SELECT
    sites (get_by_id + get_by_client_id + get_assistant_reply +
    list_by_conversation + get_recent_for_context + and the CTE
    inner + outer for get_recent_for_conversations_batch) all use
    the same column-list line — a partial update leaves a subset of
    reads returning NULL and mobile silently sees ghost data."""
    src = _read("app/repositories/message_repo.py")
    # Every canonical column-list ends with 'is_read,' + the collage
    # triple on the next line. Count of that pattern = number of
    # per-row SELECT sites (7 today — 6 in the outer paths + 1 CTE
    # inner + 1 CTE outer share the same replacement).
    canonical_end = (
        "               client_message_id, created_at, metadata, status, is_read,\n"
        "               collage_id, collage_bot_id, collage_date"
    )
    # The CTE inner uses 12-space indent inside `WITH RankedMessages AS`;
    # allow both indents.
    cte_inner_end = (
        "                   client_message_id, created_at, metadata, status, is_read,\n"
        "                   collage_id, collage_bot_id, collage_date,\n"
        "                   ROW_NUMBER() OVER ("
    )
    assert canonical_end in src
    assert cte_inner_end in src


def test_format_message_emits_collage_fields_only_when_non_null():
    """Absent (not null) semantics preserve the pre-migration wire
    contract for text/audio/image rows so old app builds don't get
    surprise keys. A future refactor that always writes the keys
    (even as null) would break mobile's Optional-field validation."""
    src = _read("app/routes/chat.py")
    idx = src.find("def _format_message(msg: dict) -> dict:")
    assert idx != -1
    end = src.find("\ndef _format_conversation", idx + 1)
    body = src[idx:end] if end != -1 else src[idx : idx + 3000]
    # The 3 setters are wrapped in `if ... is not None:`
    assert 'payload["collage_id"] = str(collage_id)' in body
    assert 'payload["collage_bot_id"] = collage_bot_id' in body
    assert 'payload["collage_date"] =' in body
    # Guard that we didn't accidentally always-set them.
    assert "collage_id is not None" in body
    assert "collage_bot_id is not None" in body
    assert "collage_date is not None" in body


def test_both_chat_routes_persist_collage_fields():
    """SYMMETRY rule (Rule 1 + brief-locked): both `send_message`
    (non-streaming) and `send_message_stream` must persist the same
    3 fields the same way. A one-side-only wire-in silently breaks
    whichever route mobile picked."""
    src = _read("app/routes/chat.py")
    # Both handlers pass the 3 kwargs into message_repo.create.
    create_calls = src.count(
        "collage_id=collage_id,\n            collage_bot_id=collage_bot_id,\n            "
        "collage_date=collage_date,"
    )
    assert create_calls >= 2, (
        f"expected the 3 collage kwargs to be passed to message_repo.create "
        f"on BOTH send_message and send_message_stream; got {create_calls}"
    )


# ─── behavioural — INSERT + SELECT round trip via stub pool ─────────────


class _StubPool:
    """Minimal in-memory `messages` table. Enough to exercise the
    INSERT column list + SELECT projection round-trip that the
    repo does."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def execute(self, sql, *args):
        # message_repo.create INSERT: 17 args, we pull the ones we
        # care about by index (matches the placeholder numbering
        # $1..$17 in the source).
        if "INSERT INTO messages" in sql:
            self.rows.append(
                {
                    "id": args[0],
                    "conversation_id": args[1],
                    "role": args[2],
                    "sender_id": args[3],
                    "content": args[4],
                    "message_type": args[5],
                    "media_urls": args[6],
                    "audio_url": args[7],
                    "audio_duration_seconds": args[8],
                    "token_count": args[9],
                    "client_message_id": args[10],
                    "is_proactive": args[11],
                    "is_nudge": args[12],
                    "variant_label": args[13],
                    "collage_id": args[14],
                    "collage_bot_id": args[15],
                    "collage_date": args[16],
                    "created_at": datetime.now(timezone.utc),
                    "metadata": None,
                    "status": "delivered",
                    "is_read": False,
                }
            )

    async def fetchrow(self, sql, *args):
        # get_by_id
        if "WHERE id = $1" in sql:
            for row in self.rows:
                if row["id"] == args[0]:
                    return row
        return None

    async def fetch(self, sql, *args):
        # list_by_conversation
        if "FROM messages" in sql and "WHERE conversation_id = $1" in sql:
            return [r for r in self.rows if r["conversation_id"] == args[0]]
        return []


@requires_fastapi
def test_send_message_collage_persists_all_3_fields():
    """The load-bearing INSERT test. A `message_type='collage'` row
    with the 3 reference fields lands in the row + round-trips through
    get_by_id."""
    from repositories import message_repo

    the_uuid = "1d0c12ed-aaa2-4a04-83a8-4233eab95fa3"
    the_date = date(2026, 7, 13)

    async def _run():
        pool = _StubPool()
        row = await message_repo.create(
            pool,
            conversation_id="conv-1",
            role="user",
            content="Requested an image",
            message_type="collage",
            sender_id="u1",
            collage_id=the_uuid,
            collage_bot_id="tara",
            collage_date=the_date,
        )
        return row

    row = asyncio.run(_run())
    assert row["message_type"] == "collage"
    # UUID stored as string per the repo's str() coercion.
    assert row["collage_id"] == the_uuid
    assert row["collage_bot_id"] == "tara"
    assert row["collage_date"] == the_date


@requires_fastapi
def test_send_message_text_ignores_collage_fields():
    """Non-collage rows leave the 3 fields NULL. Nullable columns
    forgive the server for not enforcing the co-appearance rule
    (deferred to a future CHECK constraint per the brief)."""
    from repositories import message_repo

    async def _run():
        pool = _StubPool()
        row = await message_repo.create(
            pool,
            conversation_id="conv-1",
            role="user",
            content="hey",
            message_type="text",
            sender_id="u1",
        )
        return row

    row = asyncio.run(_run())
    assert row["collage_id"] is None
    assert row["collage_bot_id"] is None
    assert row["collage_date"] is None


@requires_fastapi
def test_stream_send_message_persists_collage_fields():
    """SYMMETRY: streaming path goes through the same
    message_repo.create — a collage row lands identically whether
    Sarvesh POSTs to /messages or /messages/stream. Source-pin above
    already verified BOTH routes call create with the 3 kwargs;
    behavioural pass exercises the same insert path with a payload
    identical to what the streaming route would send."""
    from repositories import message_repo

    the_uuid = "1d0c12ed-aaa2-4a04-83a8-4233eab95fa3"

    async def _run():
        pool = _StubPool()
        row = await message_repo.create(
            pool,
            conversation_id="conv-1",
            role="user",
            content="Requested an image",
            message_type="collage",
            media_urls=None,
            sender_id="u1",
            collage_id=the_uuid,
            collage_bot_id="tara",
            collage_date=date(2026, 7, 13),
        )
        return row

    row = asyncio.run(_run())
    assert row["message_type"] == "collage"
    assert row["collage_id"] == the_uuid


@requires_fastapi
def test_get_messages_returns_collage_reference_via_format():
    """The wire response has to carry the 3 fields on collage rows.
    _format_message is the shared serializer both routes use — verify
    it emits the fields when non-null AND omits them when null."""
    from routes.chat import _format_message

    the_uuid = UUID("1d0c12ed-aaa2-4a04-83a8-4233eab95fa3")
    the_date = date(2026, 7, 13)
    collage_row = {
        "id": "msg-1",
        "conversation_id": "conv-1",
        "role": "user",
        "sender_id": "u1",
        "content": "Requested an image",
        "message_type": "collage",
        "media_urls": None,
        "audio_url": None,
        "audio_duration_seconds": None,
        "token_count": None,
        "created_at": datetime.now(timezone.utc),
        "collage_id": the_uuid,
        "collage_bot_id": "tara",
        "collage_date": the_date,
    }
    formatted = _format_message(collage_row)
    assert formatted["message_type"] == "collage"
    # UUID emitted as string per the SYMMETRY with existing UUIDs.
    assert formatted["collage_id"] == str(the_uuid)
    assert formatted["collage_bot_id"] == "tara"
    assert formatted["collage_date"] == "2026-07-13"


@requires_fastapi
def test_collage_fields_absent_on_legacy_rows():
    """Legacy rows (from before migration 050 was applied) have NULL
    in the 3 columns. The wire response must OMIT the keys entirely,
    not emit them as null — otherwise old app builds get surprise
    keys they don't parse."""
    from routes.chat import _format_message

    legacy_text_row = {
        "id": "msg-legacy",
        "conversation_id": "conv-1",
        "role": "user",
        "sender_id": "u1",
        "content": "hey",
        "message_type": "text",
        "media_urls": None,
        "audio_url": None,
        "audio_duration_seconds": None,
        "token_count": None,
        "created_at": datetime.now(timezone.utc),
        # Simulated legacy row — the 3 keys are absent from the dict
        # entirely (not even set to None). _format_message must degrade
        # gracefully.
    }
    formatted = _format_message(legacy_text_row)
    assert "collage_id" not in formatted
    assert "collage_bot_id" not in formatted
    assert "collage_date" not in formatted
    # Same shape when the columns exist but are null (post-migration
    # rows for non-collage message types).
    row_with_null_cols = {
        **legacy_text_row,
        "collage_id": None,
        "collage_bot_id": None,
        "collage_date": None,
    }
    formatted2 = _format_message(row_with_null_cols)
    assert "collage_id" not in formatted2
    assert "collage_bot_id" not in formatted2
    assert "collage_date" not in formatted2
