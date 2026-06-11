"""Strategy doc Item C — Coach grounding LIMIT 60 → 20 trim.

The Coach service's `_format_conv_excerpt` already caps the rendered
window to 10 conversations × 6 turns; the historical LIMIT 60 was
over-fetching by 3x with no signal gain. Trimming to 20 saves 1-2s of
asyncpg row materialization on big-history sessions (downstream cap
was already binding).

Source-pin both fetch sites — opening (POST /conversations/{bot_id})
and per-turn (POST /conversations/{id}/messages) — so a future refactor
that bumps either one back to 60 is caught here.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_creator_coach_opening_fetches_with_limit_20():
    """The opening grounding fetch (POST /conversations/{bot_id}) caps
    at LIMIT 20. The pre-trim LIMIT 60 added 1-2s on bots with thousands
    of message rows because asyncpg materialises every row even though
    only the first 10 conversations × 6 turns are rendered."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def create_coach_session(")
    # The opening fetch lives inside this handler — find the LIMIT after.
    handler = src[pos : pos + 4000]
    # The opening fetch site's LIMIT
    assert "LIMIT 20\n" in handler


def test_creator_coach_send_message_fetches_with_limit_20():
    """The per-turn grounding fetch (POST /conversations/{id}/messages)
    caps at LIMIT 20 — paired with the opening site so both Coach
    surfaces stay symmetric."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def send_coach_message(")
    handler = src[pos : pos + 5000]
    assert "LIMIT 20\n" in handler


def test_no_residual_limit_60_in_coach_route():
    """Belt-and-braces — guard against a future refactor reintroducing
    the old window in any new fetch added to this file."""
    src = _read("app/routes/creator_coach.py")
    assert "LIMIT 60" not in src, (
        "creator_coach.py reintroduced LIMIT 60 — strategy doc Item C "
        "set the binding cap at 20 (downstream renderer already caps "
        "to 10 convs × 6 turns)."
    )
