"""Phase 12 (Task C) — per-archetype response quality tuning.

Pins the tuning dict + the per-archetype guardrails added to ARCHETYPE_PROMPTS
so a future refactor can't silently flatten them.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_tuning_for_known_archetypes():
    from services.soul_file import tuning_for, ARCHETYPE_TUNING

    for archetype in ("companion", "advisor", "entertainer", "educator", "creator"):
        t = tuning_for(archetype)
        assert t is not None, f"{archetype} should have tuning"
        assert 0.0 <= t["temperature"] <= 1.0
        # The eval gap analysis showed verbose replies tank scores;
        # nothing in the dict should be over 2048 (config default).
        assert 300 <= t["max_tokens"] <= 1500
        # Sanity: the dict reachable via the helper matches the source
        assert ARCHETYPE_TUNING[archetype] == t


def test_tuning_for_unknown_archetype_returns_none():
    """Unknown / NULL category falls back to config defaults — the lookup
    must return None so the caller knows to use config values."""
    from services.soul_file import tuning_for

    assert tuning_for(None) is None
    assert tuning_for("") is None
    assert tuning_for("some-future-category-that-doesnt-exist") is None


def test_tuning_for_handles_casing_and_whitespace():
    """Postgres rows may have inconsistent casing; the helper must
    normalize so the contract works regardless of how the DB stored it."""
    from services.soul_file import tuning_for

    assert tuning_for("COMPANION") == tuning_for("companion")
    assert tuning_for("  Advisor  ") == tuning_for("advisor")


def test_archetype_prompts_carry_sentence_caps():
    """Eval gap: helpful=2.65 was weakest, often because bots wrote essays
    instead of solving the ask. Each archetype prompt now embeds a sentence
    cap so the LLM can't drift."""
    from services.soul_file import ARCHETYPE_PROMPTS

    for archetype, body in ARCHETYPE_PROMPTS.items():
        # Some form of "at most N sentences" must appear in the prompt
        assert "at most 3 sentences" in body or "at most 4 sentences" in body, (
            f"{archetype} prompt is missing its sentence cap"
        )


def test_educator_prompt_includes_few_shot_example():
    """Educator was the archetype most likely to ramble in the eval. The
    prompt now embeds a worked example — both English and Hinglish — so the
    model can copy the shape."""
    from services.soul_file import ARCHETYPE_PROMPTS

    educator = ARCHETYPE_PROMPTS["educator"]
    assert "Example exchange" in educator
    assert "recursion" in educator.lower()
    # Hinglish line, helps the language-mirror score too
    assert "Hinglish" in educator or "Haan" in educator or "AI sach" in educator


def test_global_rules_enumerate_indian_languages():
    """language_match was 3.10/5 in eval. The reinforced rule now lists the
    specific languages mobile users in our market actually speak."""
    from services.soul_file import GLOBAL_RULES

    for lang in ("Hinglish", "Hindi", "Telugu", "Tamil"):
        assert lang in GLOBAL_RULES, f"language list missing {lang}"
