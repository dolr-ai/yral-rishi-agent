"""Phase 7.7 — bot quality scorer.

Pure-function pins for the constants + the format helpers. The DB +
Gemini portions are exercised against the live cluster via the deploy
verification step.
"""


def test_scoring_constants_in_sensible_range():
    """Sample 20 conversations and 3 turn pairs each. Above these the
    nightly Gemini judge cost balloons; below them the score is noisy."""
    from services.quality_scorer import (
        SAMPLE_CONVERSATIONS,
        TURN_PAIRS_PER_CONVERSATION,
        JUDGE_CONCURRENCY,
    )

    assert 10 <= SAMPLE_CONVERSATIONS <= 50
    assert 1 <= TURN_PAIRS_PER_CONVERSATION <= 10
    assert 1 <= JUDGE_CONCURRENCY <= 20


def test_initial_delay_avoids_startup_thrash():
    """Loop sleeps ~15 min before the first pass so a rolling deploy doesn't
    immediately fire a big batch of Gemini calls."""
    from services.quality_scorer import INITIAL_DELAY_SEC, SCORING_INTERVAL_SEC

    assert INITIAL_DELAY_SEC >= 60
    assert SCORING_INTERVAL_SEC == 24 * 60 * 60


def test_coach_format_quality_score_with_real_row():
    """Coach renders the latest score into the META_PROMPT. The block must
    surface all four scores + the sample-size context."""
    from services.coach import _format_quality_score

    score = {
        "score_overall": 3.82,
        "score_in_character": 4.12,
        "score_response_quality": 3.51,
        "score_engagement": 3.83,
        "sample_size": 47,
        "last_n_conversations": 18,
    }
    block = _format_quality_score(score)
    assert "3.82" in block
    assert "4.12" in block
    assert "3.51" in block
    assert "3.83" in block
    assert "47" in block
    assert "18" in block


def test_coach_format_quality_score_none_gives_hint():
    """When the bot hasn't been scored yet the coach gets a placeholder so
    the META_PROMPT format-string still substitutes cleanly."""
    from services.coach import _format_quality_score

    out = _format_quality_score(None)
    assert "no score yet" in out.lower()
