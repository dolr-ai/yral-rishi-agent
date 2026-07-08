"""Source-pin + behavior tests for services/theme_generator.

The theme generator is on the user hot path (fires from
request_images route + nightly pre-gen loop), and it drives the
whole product bet: if the theme is off-brand or filter-tripping,
users see a bad batch. These tests lock the guardrails.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

MODULE = Path(__file__).parent.parent / "app" / "services" / "theme_generator.py"


def _src() -> str:
    return MODULE.read_text()


def test_prompt_requires_lora_trigger_word_at_start():
    """The LoRA identity anchor only fires when the prompt STARTS with
    the trigger word. If we drop this instruction, the LLM will
    happily emit prompts without TAARA, and the anchor stage will
    generate a generic-lookalike Tara — the exact bug we hit on
    2026-07-06."""
    body = _src()
    assert "MUST begin with the exact trigger word" in body, (
        "trigger-word-at-start rule missing — LoRA identity anchor "
        "will fail on themes without the trigger prefix"
    )
    # Validator also enforces this at runtime
    assert "startswith(trigger)" in body, "validator's trigger-prefix check removed"


def test_prompt_lists_forbidden_words_from_2026_07_06_filter_table():
    """Nano-banana-pro refuses lingerie/sheer/boudoir triggers. This
    list is empirically derived (12/12 successful nano runs used
    rewrites). If the LLM emits any of them, generation aborts —
    validator MUST reject."""
    body = _src()
    for word in ("lingerie", "sheer", "boudoir", "sensual", "nude", "topless"):
        assert word in body, (
            f"forbidden word {word!r} not listed — validator may allow "
            "prompts that trip nano-banana-pro's safety filter"
        )


def test_prompt_enforces_clothed_constraint():
    """Design §2.5: in-app collage is suggestive-but-clothed;
    explicit belongs on amorae.ai. If the prompt drops the CLOTHED
    instruction, the LLM has no signal to keep her dressed."""
    body = _src()
    assert "clothed" in body.lower(), (
        "clothed constraint missing from LLM prompt — content-safety "
        "gate for App Store / Play Store is broken"
    )


def test_prompt_asks_for_variety_from_recent_themes():
    """Users shouldn't see the same location twice in a week. The
    prompt MUST feed the LLM the recent-themes list AND instruct it
    to pick a new location + outfit combination."""
    body = _src()
    assert "RECENT THEMES" in body, (
        "recent-themes block missing — LLM will repeat scenes"
    )
    assert "recent_themes" in body, "recent_themes template hole missing"
    assert "recent_themes(pool, bot_id" in body, (
        "recent_themes lookup call removed — variety instruction is toothless"
    )


def test_prompt_gives_setting_variety_categories():
    """Without setting categories, the LLM defaults to a narrow band
    (mostly beach). Force it across European coastal, resort, urban,
    exotic, and editorial buckets so themes stay fresh over months."""
    body = _src()
    for category_marker in (
        "European coastal",
        "luxury resort",
        "urban",
        "exotic",
        "editorial",
    ):
        assert category_marker in body, (
            f"setting category {category_marker!r} missing — theme "
            "variety will collapse over months of daily use"
        )


def test_validator_rejects_missing_trigger():
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    assert (
        tg._validate_theme(
            "Tara at Capri beach in a bikini, editorial swimwear photography, "
            "golden hour, 85mm lens",
            trigger="TAARA",
        )
        is None
    ), "validator accepted theme missing TAARA trigger prefix"


def test_validator_rejects_forbidden_words():
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    bad = "TAARA at a Milan hotel suite in sheer lingerie, editorial photography, dusk, 85mm lens"
    assert tg._validate_theme(bad, trigger="TAARA") is None, (
        "validator accepted a theme containing 'lingerie' — filter will trip"
    )


def test_validator_rejects_no_clothing_anchor():
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    bad = "TAARA on a Santorini clifftop at blue hour, editorial photography, 85mm lens, shallow depth of field"
    # no bikini/swimsuit/slip/kaftan/etc. → reject
    assert tg._validate_theme(bad, trigger="TAARA") is None, (
        "validator accepted a theme with no clothing anchor — output unpredictable"
    )


def test_validator_rejects_truncated_theme_ending_in_comma():
    """Regression: Gemini's first prod call 2026-07-08 returned
    'TAARA in a designer cutout swimsuit,' — 36 chars, ended with a
    comma because the LLM stopped mid-sentence. The original validator
    accepted it (passed trigger + clothing + length checks) and shipped
    a fragmentary prompt to nano-banana-pro. The fix bumps the length
    floor to 60 AND rejects any theme not ending on letter/digit/period."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    truncated = "TAARA in a designer cutout swimsuit,"
    assert tg._validate_theme(truncated, trigger="TAARA") is None, (
        "validator accepted the exact 2026-07-08 truncated theme — this is a regression"
    )


def test_validator_rejects_theme_missing_editorial_qualifier():
    """The whole product bet is 'premium editorial-magazine aesthetic'.
    A theme without an editorial qualifier drops the prompt's Constraint
    4 and lands generic-looking outputs."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    bad = (
        "TAARA on a Bali beach in a high-fashion bikini, playful pose "
        "with wind-swept hair, golden hour glow, 85mm lens, shallow "
        "depth of field."
    )
    # No 'editorial' or 'vogue' anywhere → reject
    assert tg._validate_theme(bad, trigger="TAARA") is None, (
        "validator accepted a theme with no editorial qualifier"
    )


def test_validator_rejects_theme_missing_lens_qualifier():
    """Same as editorial — Constraint 5 requires a lens/depth qualifier
    so nano-banana-pro produces cinematic compositions, not flat ones."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    bad = (
        "TAARA at a Milan runway backstage in a couture cocktail dress, "
        "confident pose behind the scenes, editorial fashion photography, "
        "dusk light through the studio window."
    )
    # No 'lens', 'depth of field', 'cinematic', '35mm', '50mm', '85mm'
    assert tg._validate_theme(bad, trigger="TAARA") is None, (
        "validator accepted a theme with no lens/depth qualifier"
    )


def test_validator_accepts_good_theme():
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")
    good = (
        "TAARA on a Santorini clifftop infinity pool at blue hour, "
        "wearing a designer cutout bikini, cinematic confident pose "
        "with wind-swept hair, editorial Vogue swimwear photography, "
        "85mm cinematic lens, shallow depth of field"
    )
    result = tg._validate_theme(good, trigger="TAARA")
    assert result == good, "validator rejected the reference-good theme"


def test_generate_daily_theme_falls_back_on_llm_exception():
    """LLM outage must NOT block the user. Fall back to
    config.COLLAGE_THEME_TARA silently (with a warning log)."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")

    class _FakePool:
        pass

    fake_pool = _FakePool()

    async def _boom(*a, **kw):
        raise RuntimeError("LLM outage")

    with patch.object(tg.llm_registry, "call", new=AsyncMock(side_effect=_boom)):
        with patch.object(
            tg.influencer_collage_repo,
            "recent_themes",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(
                tg.generate_daily_theme(
                    fake_pool,
                    # Tara's bot_id — has a trigger word configured
                    "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
                )
            )
    import config as _cfg

    assert result == _cfg.COLLAGE_THEME_TARA, (
        "LLM outage did not fall back to config default — user path blocked"
    )


def test_generate_daily_theme_falls_back_after_two_invalid_llm_outputs():
    """Two invalid outputs in a row → fall back. If the LLM is
    consistently emitting bad prompts (prompt-injection, drift,
    off-brand), we don't want to burn API calls forever."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")

    class _Resp:
        def __init__(self, content):
            self.content = content

    fake_pool = object()
    bad_responses = [_Resp("A cool photo of a woman"), _Resp("nothing to see here")]

    with patch.object(
        tg.llm_registry,
        "call",
        new=AsyncMock(side_effect=bad_responses),
    ):
        with patch.object(
            tg.influencer_collage_repo,
            "recent_themes",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(
                tg.generate_daily_theme(
                    fake_pool,
                    "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
                )
            )
    import config as _cfg

    assert result == _cfg.COLLAGE_THEME_TARA, (
        "two-strikes fallback broken — user path may receive an invalid theme"
    )


def test_bot_without_trigger_word_falls_back_immediately():
    """Bots without a configured LoRA trigger can't lock identity via
    the LLM path — fall back to the config default without spending
    an LLM call. Prevents the 'generic western woman' bug for any
    unconfigured bot."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    tg = importlib.import_module("services.theme_generator")

    call_mock = AsyncMock()
    with patch.object(tg.llm_registry, "call", new=call_mock):
        with patch.object(
            tg.influencer_collage_repo,
            "recent_themes",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(
                tg.generate_daily_theme(object(), "some-unknown-bot-id-not-tara")
            )
    import config as _cfg

    assert result == _cfg.COLLAGE_THEME_TARA
    assert not call_mock.called, (
        "LLM was called for an unconfigured bot — wasted spend + risky "
        "output for a bot without LoRA identity lock"
    )


def test_llm_defaults_registers_collage_theme_generator_on_gemini():
    """Source-pin the process-name registration + provider choice.
    Two places to check: the LLM_DEFAULTS dict entry (routes to
    provider + model) AND the runtime import via llm_registry so
    the constant is actually loaded, not just present in source."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    import importlib

    llm_registry = importlib.import_module("services.llm_registry")
    assert "collage_theme_generator" in llm_registry.LLM_DEFAULTS, (
        "collage_theme_generator missing from LLM_DEFAULTS — call() "
        "will not route correctly"
    )
    entry = llm_registry.LLM_DEFAULTS["collage_theme_generator"]
    assert entry["provider"] == "gemini", (
        "collage_theme_generator not routed to gemini — sync user path "
        "must default to gemini per feedback_llm_defaults_sync_paths_use_gemini"
    )
    # Also must be in PROCESS_NAMES so the leak-guard registry
    # accepts the call
    assert "collage_theme_generator" in llm_registry.PROCESS_NAMES, (
        "collage_theme_generator missing from PROCESS_NAMES tuple — "
        "llm_registry.call() will reject with unknown-process error"
    )
