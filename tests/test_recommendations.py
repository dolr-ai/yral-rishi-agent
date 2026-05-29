"""Phase 7.8 — creator recommendations.

Pure-function pins for the parser + the format helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_parse_recommendations_clean_json():
    from services.recommendations import _parse_recommendations

    text = """[
      {"weakness": "in_character 3.2 — breaks character on philosophy",
       "proposed_edit": "Add rule: never discuss the existence of AI.",
       "reasoning": "Bot derails into meta when philosophical."},
      {"weakness": "engagement 2.8 — replies don't invite continuation",
       "proposed_edit": "Add: end every reply with a small hook.",
       "reasoning": "Score is below 3."}
    ]"""
    out = _parse_recommendations(text)
    assert out is not None
    assert len(out) == 2
    for r in out:
        assert {"weakness", "proposed_edit", "reasoning"} <= set(r.keys())


def test_parse_recommendations_tolerates_wrapping_prose():
    """LLMs sometimes prefix with 'Here are my recommendations:' etc."""
    from services.recommendations import _parse_recommendations

    text = (
        'Here are my recommendations: [{"weakness": "x", '
        '"proposed_edit": "y", "reasoning": "z"}] — let me know!'
    )
    out = _parse_recommendations(text)
    assert out is not None
    assert len(out) == 1


def test_parse_recommendations_returns_none_for_garbage():
    from services.recommendations import _parse_recommendations

    assert _parse_recommendations("") is None
    assert _parse_recommendations("here are my recommendations") is None
    assert _parse_recommendations("[]") is None  # empty array → None
    assert _parse_recommendations('[{"weakness": "x"}]') is None  # missing keys


def test_parse_recommendations_drops_invalid_items():
    """If the model emits a mix of valid + invalid items, keep the valid ones."""
    from services.recommendations import _parse_recommendations

    text = """[
      {"weakness": "ok", "proposed_edit": "ok", "reasoning": "ok"},
      {"weakness": "missing fields"},
      {"weakness": "another ok", "proposed_edit": "y", "reasoning": "z"}
    ]"""
    out = _parse_recommendations(text)
    assert out is not None
    assert len(out) == 2


def test_format_quality_score_block_with_data():
    from services.recommendations import _format_quality_score_block

    block = _format_quality_score_block(
        {
            "score_overall": 3.4,
            "score_in_character": 3.2,
            "score_response_quality": 3.5,
            "score_engagement": 3.5,
            "sample_size": 47,
            "last_n_conversations": 18,
        }
    )
    assert "3.40" in block
    assert "3.20" in block
    assert "47" in block


def test_format_quality_score_block_none_gives_hint():
    from services.recommendations import _format_quality_score_block

    assert "no score yet" in _format_quality_score_block(None).lower()
