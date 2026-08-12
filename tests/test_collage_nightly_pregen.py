"""Source-pin + behavior tests for services.collage_nightly_pregen.

The pre-gen loop spends money (~$0.27 per bot per pass). Every gate
that stops it from over-spending or from running the wrong bots MUST
be locked so a future refactor can't accidentally regress the safety
posture.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

MODULE_PATH = (
    Path(__file__).parent.parent / "app" / "services" / "collage_nightly_pregen.py"
)


def _src() -> str:
    return MODULE_PATH.read_text()


def test_pregen_hour_is_04_utc():
    """Design doc §4 anchors on 04:00 UTC. Anything else means users
    on the peak morning-usage window (India 09:30 IST, Southeast Asia
    similar) get a 45-65s wait for the first Request Images tap of the
    day, defeating the whole point of pre-gen."""
    body = _src()
    assert "_PREGEN_HOUR_UTC = 4" in body, (
        "04:00 UTC pre-gen hour drifted — users in India/SE Asia will "
        "start seeing on-demand wait times for morning taps"
    )


def test_registered_in_kill_switch_default_off():
    """The loop spends money on Replicate + Gemini each day. If it
    silently defaults ON, a fresh deploy or a rollback surprise-bills
    the operator (2026-05-29 Gemini burn lesson). MUST ship dormant."""

    import importlib

    ks = importlib.import_module("kill_switch")
    assert "collage_pregen" in ks._PER_LOOP_KEYS, (
        "collage_pregen kill-switch key missing — ops has no way to stop "
        "the loop without redeploying"
    )
    assert ks._PER_LOOP_KEYS["collage_pregen"] == "ENABLE_COLLAGE_PREGEN_LOOP", (
        "env var name changed — runbook + ops docs will point at the wrong key"
    )
    assert "collage_pregen" in ks._DEFAULT_OFF_LOOPS, (
        "collage_pregen not in DEFAULT_OFF set — new deploys will silently "
        "spend money before Sarvesh's mobile integration is ready"
    )


def test_only_active_lora_bots_are_pregen_candidates():
    """Iterating all bots would burn money on demoed/banned/lora-less
    bots. Filter MUST be `lora_weights_url IS NOT NULL AND is_active = 'active'`."""
    body = _src()
    assert "lora_weights_url IS NOT NULL" in body, (
        "candidate filter dropped LoRA-null guard — pre-gen will burn "
        "$0.27 on bots that produce identityless output"
    )
    assert "is_active = 'active'" in body, (
        "candidate filter dropped is_active guard — banned + discontinued "
        "bots would still be pre-generated"
    )


def test_seconds_until_next_04_utc_correct_when_before():

    import importlib

    m = importlib.import_module("services.collage_nightly_pregen")
    fake_now = datetime(2026, 7, 8, 2, 30, 0, tzinfo=timezone.utc)  # 02:30 UTC
    with patch.object(m, "datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = datetime  # let other datetime uses pass through
        secs = m._seconds_until_next_pregen_utc()
    # 04:00 - 02:30 = 1h30m = 5400s
    assert secs == 5400, f"expected 5400s, got {secs}"


def test_seconds_until_next_04_utc_correct_when_after():

    import importlib

    m = importlib.import_module("services.collage_nightly_pregen")
    fake_now = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)  # 10:00 UTC
    with patch.object(m, "datetime") as dt:
        dt.now.return_value = fake_now
        dt.side_effect = datetime
        secs = m._seconds_until_next_pregen_utc()
    # next 04:00 UTC is tomorrow at 04:00 → 18h = 64800s
    assert secs == 64800, f"expected 64800s, got {secs}"


def test_pregen_skips_bots_with_succeeded_row_today():
    """Idempotent semantic: pre-gen fills the gap. If the row is
    already 'succeeded' (e.g. an early user requested at 03:00 UTC
    before the 04:00 pre-gen), we MUST skip to avoid a double spend."""

    import importlib

    m = importlib.import_module("services.collage_nightly_pregen")

    class _Row:
        def __getitem__(self, k):
            return {"id": "tara-uuid", "lora_weights_url": "yral/tara-lora-v1:V"}[k]

    fake_pool = MagicMock()

    async def fake_list(pool):
        return [{"id": "tara-uuid", "lora_weights_url": "yral/tara-lora-v1:V"}]

    with patch.object(m, "_list_pregen_candidates", new=fake_list):
        # Existing row already 'succeeded' — MUST skip, MUST NOT call
        # theme_generator or orchestrate (would burn money).
        gen_mock = AsyncMock(return_value="something")
        orchestrate_mock = AsyncMock(return_value={"status": "ready"})
        get_mock = AsyncMock(return_value={"state": "succeeded"})

        with (
            patch("repositories.influencer_collage_repo.get", new=get_mock),
            patch("services.theme_generator.generate_daily_theme", new=gen_mock),
            patch("services.image_collage.orchestrate", new=orchestrate_mock),
        ):
            stats = asyncio.run(m.pregen_one_pass(fake_pool))

    assert stats == {
        "candidates": 1,
        "generated": 0,
        "skipped": 1,
        "failed": 0,
    }, stats
    assert not gen_mock.called, (
        "theme_generator called for already-succeeded bot — pre-gen "
        "burned Gemini spend that would have been redundant"
    )
    assert not orchestrate_mock.called, (
        "orchestrate called for already-succeeded bot — pre-gen would "
        "have burned $0.27 on a redundant Replicate run"
    )


def test_pregen_generates_when_no_row_exists():
    """Happy path: no row for today → theme_generator → orchestrate
    with `consume_quota=False`. The synthetic user_id doesn't consume
    any real user's daily quota."""

    import importlib

    m = importlib.import_module("services.collage_nightly_pregen")

    async def fake_list(pool):
        return [{"id": "tara-uuid", "lora_weights_url": "yral/tara-lora-v1:V"}]

    theme_str = (
        "TAARA on a Dubai rooftop pool deck, in designer swimwear, at "
        "golden hour, editorial swimwear photography, shallow depth of field."
    )

    with patch.object(m, "_list_pregen_candidates", new=fake_list):
        gen_mock = AsyncMock(return_value=theme_str)
        orchestrate_mock = AsyncMock(return_value={"status": "ready"})
        get_mock = AsyncMock(return_value=None)

        with (
            patch("repositories.influencer_collage_repo.get", new=get_mock),
            patch("services.theme_generator.generate_daily_theme", new=gen_mock),
            patch("services.image_collage.orchestrate", new=orchestrate_mock),
        ):
            stats = asyncio.run(m.pregen_one_pass(MagicMock()))

    assert stats["generated"] == 1
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    # Assert consume_quota=False so pregen never burns real quota
    call_kwargs = orchestrate_mock.await_args.kwargs
    assert call_kwargs["consume_quota"] is False, (
        "pregen orchestrate() called with consume_quota=True — would "
        "consume the synthetic __pregen__ user's daily quota and could "
        "later be mis-attributed"
    )
    assert call_kwargs["user_id"] == "__pregen__", (
        "synthetic user_id changed — audit trail in user_image_requests "
        "would lose the ability to distinguish pregen from real user hits"
    )
    assert call_kwargs["theme"] == theme_str
    assert call_kwargs["lora_weights_url"] == "yral/tara-lora-v1:V"


def test_pregen_counts_failures_but_continues():
    """If bot A fails, bot B MUST still get a chance. A single Replicate
    5xx should not stop the whole nightly sweep."""

    import importlib

    m = importlib.import_module("services.collage_nightly_pregen")

    async def fake_list(pool):
        return [
            {"id": "bot-A", "lora_weights_url": "yral/A:V"},
            {"id": "bot-B", "lora_weights_url": "yral/B:V"},
        ]

    async def fake_orchestrate(pool, **kw):
        if kw["bot_id"] == "bot-A":
            return {"status": "failed", "reason": "generator_failed"}
        return {"status": "ready"}

    with (
        patch.object(m, "_list_pregen_candidates", new=fake_list),
        patch(
            "repositories.influencer_collage_repo.get",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.theme_generator.generate_daily_theme",
            new=AsyncMock(
                return_value="TAARA at Dubai rooftop, editorial swimwear photography, 85mm lens, golden hour, wearing designer swimwear."
            ),
        ),
        patch("services.image_collage.orchestrate", new=fake_orchestrate),
    ):
        stats = asyncio.run(m.pregen_one_pass(MagicMock()))

    assert stats == {
        "candidates": 2,
        "generated": 1,
        "skipped": 0,
        "failed": 1,
    }, stats


def test_registered_in_main_lifespan():
    """The loop must be wired into main.py lifespan or it never
    starts. Also must be cancelled on shutdown or the process hangs."""
    src = (Path(__file__).parent.parent / "app" / "main.py").read_text()
    assert "collage_pregen_loop" in src, (
        "collage_pregen_loop not imported in main.py lifespan"
    )
    assert "collage_pregen_task = asyncio.create_task" in src, (
        "collage_pregen_task not created in lifespan"
    )
    assert "collage_pregen_task.cancel()" in src, (
        "collage_pregen_task not cancelled on shutdown — process hangs"
    )
