"""collage_date str→date codec fix — regression tests.

2026-07-14: Sarvesh's mobile POST /messages started 500ing with:

    asyncpg.exceptions.DataError: invalid input for query argument
    $17: '2026-07-14' ('str' object has no attribute 'toordinal')

Root cause: chat.py's send_message + send_message_stream handlers
were passing `body['collage_date']` (a wire-format ISO string) straight
to `message_repo.create`, which forwards to asyncpg's DATE codec — the
codec rejects strings at the protocol layer *before* SQL runs, so the
migration-050 `$17::date` cast can't rescue it.

PR #456's original tests exercised `message_repo.create` with a real
`date` object through a mocked pool, so this codec constraint was
never actually hit until real prod traffic did.

Fix: chat.py._parse_collage_date converts ISO string → date at the
route boundary. This suite pins that behavior.
"""

from datetime import date
from pathlib import Path

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


# ─── unit — _parse_collage_date behaviour ─────────────────────────────


@requires_fastapi
def test_parse_iso_string_returns_date():
    from routes.chat import _parse_collage_date

    result = _parse_collage_date("2026-07-14")
    assert result == date(2026, 7, 14)
    assert isinstance(result, date)


@requires_fastapi
def test_parse_none_returns_none():
    from routes.chat import _parse_collage_date

    assert _parse_collage_date(None) is None


@requires_fastapi
def test_parse_date_object_passthrough():
    """Callers that already have a date object (e.g. internal
    orchestration) get their value back unchanged — no double parse."""
    from routes.chat import _parse_collage_date

    d = date(2026, 7, 14)
    assert _parse_collage_date(d) is d


@requires_fastapi
def test_parse_malformed_raises_400():
    """Malformed collage_date returns a clean 422/400 to mobile rather
    than 500'ing at the DB codec layer. Same principle as the
    GET /collage ?date= handler at request_images.py:186-193."""
    from fastapi import HTTPException
    from routes.chat import _parse_collage_date

    with pytest.raises(HTTPException) as exc:
        _parse_collage_date("not-a-date")
    assert exc.value.status_code == 400
    assert "collage_date" in exc.value.detail.lower()


@requires_fastapi
def test_parse_empty_string_raises_400():
    from fastapi import HTTPException
    from routes.chat import _parse_collage_date

    with pytest.raises(HTTPException) as exc:
        _parse_collage_date("")
    assert exc.value.status_code == 400


# ─── source-pin — both send handlers must call the helper ──────────


def test_both_send_handlers_wrap_collage_date_in_parser():
    """SYMMETRY guard. If a new send-message handler is added or the
    body.get('collage_date') pattern is copied without the parser, the
    codec bug returns. This test fails loudly if any bare
    body.get('collage_date') survives."""
    src = _read("app/routes/chat.py")
    # Every collage_date read from the request body must be wrapped.
    # Two occurrences today (send_message + send_message_stream).
    wrapped = src.count("_parse_collage_date(body.get(\"collage_date\"))")
    raw = src.count("body.get(\"collage_date\")")
    assert wrapped >= 2, (
        f"Expected at least 2 _parse_collage_date wrappings, found {wrapped}"
    )
    # No `body.get("collage_date")` should appear OUTSIDE the wrapper.
    # Every raw read is also a wrapped read (the substring is contained).
    assert raw == wrapped, (
        f"Found {raw} bare body.get('collage_date') reads but only "
        f"{wrapped} are wrapped by _parse_collage_date — the codec bug "
        f"will return on the unwrapped site."
    )


def test_helper_is_module_level_not_local():
    """Helper must be importable — if inlined into a handler it can't
    be unit-tested and the source-pin above can't verify wrapping."""
    src = _read("app/routes/chat.py")
    assert "def _parse_collage_date(" in src
    # Module-level def (no leading indent).
    assert "\ndef _parse_collage_date(" in src


def test_datetime_import_covers_date():
    """asyncpg needs a `date` object — the module needs `date` in scope
    for the helper. Prevents a NameError regression if someone tidies
    the imports without checking."""
    src = _read("app/routes/chat.py")
    assert (
        "from datetime import date, datetime" in src
        or "from datetime import datetime, date" in src
    )
