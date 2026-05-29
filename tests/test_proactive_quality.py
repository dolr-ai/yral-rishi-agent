"""Task 2 (Phase 5 polish) — proactive message quality fix.

Pure-function pins for the new constants + prompt assembly. The cap + variety
queries are exercised against the live cluster via the deploy verification
script, not unit-tested here (asyncpg + Postgres needed).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_message_types_complete_and_ordered():
    """Spec: question / observation / story / light_topic. If a refactor
    drops one or renames, the type-hint dict-lookup would KeyError silently
    on Gemini's side — this test catches it locally first."""
    from services.proactive import PROACTIVE_MESSAGE_TYPES, TYPE_HINTS

    expected = {"question", "observation", "story", "light_topic"}
    assert set(PROACTIVE_MESSAGE_TYPES) == expected
    # Every type has a TYPE_HINTS entry
    for t in PROACTIVE_MESSAGE_TYPES:
        assert t in TYPE_HINTS
        assert len(TYPE_HINTS[t]) > 0


def test_archetype_tones_cover_all_archetypes():
    """If a new archetype lands in soul_file but not here, proactive falls
    back to generic tone — not broken, but worth surfacing as the system
    drifts. Pins the current set to make the drift visible."""
    from services.proactive import ARCHETYPE_TONE

    expected = {"companion", "advisor", "entertainer", "creator", "educator"}
    assert set(ARCHETYPE_TONE.keys()) == expected


def test_cap_constant_is_three():
    """3 unanswered proactives = the engagement loop skips this conv until
    user replies. Motorola observed 3-4 before the fix; cap = 3 prevents
    the next one from going out."""
    from repositories.message_repo import PROACTIVE_CAP_WITHOUT_REPLY

    assert PROACTIVE_CAP_WITHOUT_REPLY == 3


def test_proactive_prompt_carries_anti_recitation_guard():
    """The PROACTIVE_PROMPT now embeds the same anti-recitation language as
    Task 1's soul_file L4 change — proactive messages also went into the
    "lead with personal facts" regression. Guard against accidental softening."""
    from services.proactive import PROACTIVE_PROMPT

    assert "DO NOT lead with them" in PROACTIVE_PROMPT
    assert "DO NOT recite" in PROACTIVE_PROMPT
    assert "I remember you said" in PROACTIVE_PROMPT  # negative example
