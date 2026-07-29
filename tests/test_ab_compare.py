"""Phase 7.6 — A/B compare service.

Pure-function pins. The live judging path is exercised via the deploy
verification step (mint variant B, send some messages, hit /compare)."""


def test_min_sample_thresholds_are_safe():
    """Below MIN we'd promote on noise; above MAX we'd burn judge calls
    for diminishing returns."""
    from services.ab_compare import MIN_PER_VARIANT, MAX_PER_VARIANT

    assert 5 <= MIN_PER_VARIANT <= 20
    assert MAX_PER_VARIANT >= MIN_PER_VARIANT
    assert MAX_PER_VARIANT <= 50


def test_judge_concurrency_capped():
    """Same cap as Phase 7.7 scorer — keeps per-bot Gemini fan-out bounded."""
    from services.ab_compare import JUDGE_CONCURRENCY

    assert 1 <= JUDGE_CONCURRENCY <= 20
