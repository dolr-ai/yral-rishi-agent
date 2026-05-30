"""Phase 6.3p — nudge cap pinning.

Static checks for the cap-pattern wiring. The runtime behavior (stuck
user goes silent → 1 nudge → no more nudges → user replies → cap
resets) is exercised after deploy."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_nudge_cap_constant_exists_and_is_one():
    """Cap = 1, not 3. Different from proactive because nudges fire
    every 15 min during early-conversation idle; one try is plenty."""
    src = _read("app/repositories/message_repo.py")
    assert "NUDGE_CAP_WITHOUT_REPLY = 1" in src


def test_proactive_cap_unchanged_at_3():
    """Symmetry guard — if a refactor accidentally renames both caps,
    catch it here."""
    src = _read("app/repositories/message_repo.py")
    assert "PROACTIVE_CAP_WITHOUT_REPLY = 3" in src


def test_count_unanswered_nudge_helper_exists():
    """Mirror of count_unanswered_proactive. Both should grep at the
    same place in the file so future cap-pattern audits see them
    together."""
    src = _read("app/repositories/message_repo.py")
    assert "async def count_unanswered_nudge" in src
    assert "async def count_unanswered_proactive" in src
    # The two helpers must use the same SQL shape so behavior is
    # parallel — both gate on the user's last reply.
    assert src.count("'epoch'::timestamp") >= 2


def test_should_nudge_enforces_cap():
    """Regression guard: should_nudge must call count_unanswered_nudge
    and gate on NUDGE_CAP_WITHOUT_REPLY before returning True."""
    src = _read("app/services/nudge.py")
    assert "count_unanswered_nudge" in src
    assert "NUDGE_CAP_WITHOUT_REPLY" in src


def test_engagement_loop_sets_is_nudge_true():
    """Without is_nudge=True at the save site, count_unanswered_nudge
    counts 0 forever and the cap never engages. Pin the call shape."""
    src = _read("app/main.py")
    assert "is_nudge=True" in src


def test_migration_023_adds_is_nudge_column():
    """Migration file pins the column add + partial index. Catches
    accidental migration-number collision or file deletion."""
    src = _read("migrations/023_messages_is_nudge.sql")
    assert "ADD COLUMN IF NOT EXISTS is_nudge BOOLEAN" in src
    assert "DEFAULT FALSE" in src
    assert "WHERE is_nudge = TRUE" in src  # partial index for cap query


def test_message_repo_create_accepts_is_nudge():
    """The create() signature must accept is_nudge for the nudge loop
    to tag rows correctly. Defaults to False so all existing callers
    (regular AI replies, H2H) keep working unchanged."""
    import inspect
    from repositories import message_repo

    sig = inspect.signature(message_repo.create)
    assert "is_nudge" in sig.parameters
    assert sig.parameters["is_nudge"].default is False
