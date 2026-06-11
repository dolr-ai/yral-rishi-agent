"""2026-06-12 ~15:53 IST regression test.

Mobile expert hit a 500 applying an override-type proposal on Anastasia
(Rishi's bot). Root cause: `app/routes/creator_coach.py` was doing
`{**(inf.get("global_rule_overrides") or {}), key: value}` to build the
new overrides JSON for the audit row. But asyncpg returns JSONB columns
as raw strings (no JSON codec is registered in `app/database.py`), so
for any bot whose `global_rule_overrides` column had been populated by
a prior apply, `inf.get(...)` returns a `str` — and `{**str}` raises
TypeError at the dict-spread level. Uncaught → FastAPI 500 generic body.

The same defensive `if isinstance(..., str): json.loads(...)` pattern
already exists in:
  - `app/services/soul_file.py:_render_global_rules` (the rule-render
    path for chat-send)
  - `app/routes/creator_coach.py:505-514` (the sections-apply path
    landed in Bucket 2 PR-2 #366)

The override-apply path missed it; this test ensures it can't regress.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

CREATOR_COACH = (APP_DIR / "routes" / "creator_coach.py").read_text()


def test_override_apply_handles_jsonb_returned_as_string():
    """The override-apply branch in creator_coach.py MUST defensively
    parse `global_rule_overrides` as a string when asyncpg returns it
    that way. Without this guard, the `{**inf.get(...)}` dict-spread
    crashes with TypeError → 500."""
    # The guard pattern must be present
    assert "isinstance(existing_overrides, str)" in CREATOR_COACH, (
        "override apply must check if global_rule_overrides is a string "
        "(asyncpg returns JSONB as raw str)"
    )
    assert "_json.loads(existing_overrides)" in CREATOR_COACH, (
        "override apply must json.loads() the string form before spread"
    )


def test_override_apply_does_not_dict_spread_unwrapped_jsonb():
    """The bug was `{**(inf.get("global_rule_overrides") or {}), key: value}`.
    The fix replaces that with a variable that's been guaranteed to be
    a dict. The naked spread on the raw column value must NOT come back."""
    assert '{**(inf.get("global_rule_overrides") or {})' not in CREATOR_COACH, (
        "naked dict-spread on raw global_rule_overrides column value will "
        "crash for bots whose column has been populated (asyncpg returns "
        "JSONB as str)"
    )


def test_override_apply_logs_malformed_blob_gracefully():
    """If the column is genuinely corrupt (not parseable JSON), we
    should fall back to empty + log a warning, not 500 the apply."""
    # The except clause for malformed JSON must exist
    assert "malformed global_rule_overrides JSONB" in CREATOR_COACH, (
        "malformed-JSONB recovery path must log warning + fall through"
    )
    assert "logger.warning" in CREATOR_COACH, (
        "malformed recovery must use logger.warning, not silent pass"
    )


def test_sections_apply_already_guards_jsonb_string():
    """Pin the pattern in the sections-apply branch (which was correct
    in #366) so a future refactor doesn't accidentally remove BOTH the
    section-apply guard AND the new override guard at the same time."""
    assert "isinstance(live_sections_raw, str)" in CREATOR_COACH, (
        "sections apply must check if system_instructions_sections is a "
        "string (asyncpg returns JSONB as raw str) — same pattern as override"
    )
