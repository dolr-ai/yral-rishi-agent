"""Phase 12 (Task C) — per-archetype response quality tuning.

Pins the tuning dict + the per-archetype guardrails added to ARCHETYPE_PROMPTS
so a future refactor can't silently flatten them.
"""


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


def test_archetype_prompts_do_not_hardcode_sentence_caps():
    """First-pass Phase 12 added explicit `at most N sentences` caps to each
    archetype prompt; the 2026-05-29 re-eval showed it BACKFIRED (overall
    3.62 vs morning's 3.77). Caps forced cramped replies that didn't solve
    the user's ask. GLOBAL_RULES' soft '1-3 sentences max' is the only
    length guidance now — if a future PR re-adds per-archetype caps, this
    test fails so we don't silently re-introduce the regression."""
    from services.soul_file import ARCHETYPE_PROMPTS

    for archetype, body in ARCHETYPE_PROMPTS.items():
        assert "at most 3 sentences" not in body, (
            f"{archetype} prompt re-introduces a sentence cap that regressed quality"
        )
        assert "at most 4 sentences" not in body, (
            f"{archetype} prompt re-introduces a sentence cap that regressed quality"
        )


def test_archetype_max_tokens_uniform_and_generous():
    """Rollback target: 1500 across all archetypes. Below 1000 risks cutting
    off useful replies; above 2048 leaves cache-prefix territory."""
    from services.soul_file import ARCHETYPE_TUNING

    for archetype, t in ARCHETYPE_TUNING.items():
        assert 1000 <= t["max_tokens"] <= 2048, (
            f"{archetype} max_tokens={t['max_tokens']} outside safe range"
        )


def test_educator_prompt_includes_few_shot_example():
    """Educator was the archetype most likely to ramble in the eval. The
    prompt embeds a worked example so the model can copy the shape.

    Generalized 2026-06-04: the Hinglish second example was dropped to keep
    the archetype language-agnostic — global creators should not inherit an
    India-specific signal. The English recursion example is sufficient to
    prime the analogies-first behaviour the educator archetype needs.
    """
    from services.soul_file import ARCHETYPE_PROMPTS

    educator = ARCHETYPE_PROMPTS["educator"]
    assert "Example exchange" in educator
    assert "recursion" in educator.lower()
    assert "Russian dolls" in educator, (
        "expected the recursion analogy as the worked example"
    )


def test_global_rules_mirror_any_user_language():
    """Generalized 2026-06-04: GLOBAL_RULES no longer enumerates specific
    Indian languages. The load-bearing instruction is `mirror exactly` so
    the rule works for any user-language pair (Hinglish stays handled,
    Spanglish / Singlish / Arabish are now equally handled).
    """
    from services.soul_file import GLOBAL_RULES

    # Core instruction must remain.
    assert "Mirror the user's language exactly" in GLOBAL_RULES, (
        "language-mirror instruction must remain the load-bearing rule"
    )
    # Mid-message code-switching must remain explicit so the model knows to
    # mirror two-language messages without enumerating language names.
    assert "code-switching" in GLOBAL_RULES, (
        "must keep explicit code-switching instruction"
    )
    # Guard against accidental re-introduction of the India enumeration:
    # listing language names creates a regional default that mis-tunes global
    # bots. If we ever want region-specific behavior, do it via a per-
    # influencer region config, not by hardcoding a list here.
    forbidden_enumeration = ("Telugu", "Tamil", "Bengali", "Marathi")
    found = [lang for lang in forbidden_enumeration if lang in GLOBAL_RULES]
    assert not found, (
        f"GLOBAL_RULES should not enumerate specific languages "
        f"(found: {found}); use generic mirror instruction instead"
    )
