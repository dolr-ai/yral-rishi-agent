"""Collage fallback-to-most-recent-succeeded — regression tests.

Incident context (2026-07-13): Replicate/Google tightened the
nano-banana-pro content-safety filter mid-week. Tara's nightly
pre-gen refused all 6 images → today's row landed as state='failed'.
POST /request-images then saw the failed row and fell back to
synchronous regen (which also failed → 502 → Sarvesh blocked on
mobile integration).

Fix: when today's row is failed, serve the bot's most-recent
succeeded row within COLLAGE_FALLBACK_MAX_DAYS instead of bubbling
502. Only bubble the real "failed" if no recent success exists.

Tests cover the four brief-mandated scenarios plus a couple of
source-pins.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


TODAY = datetime.now(timezone.utc).date()


# ─── source-pin ─────────────────────────────────────────────────────────


def test_repo_has_get_latest_succeeded_with_window_and_ordering():
    """The fallback query must:
      * filter state='succeeded'
      * restrict to CURRENT_DATE - $2::int
      * order DESC by generation_date + LIMIT 1
    A silent drop of any of these would either serve a stale row from
    2 months ago (missing window filter) or fall back to a chronological
    first-hit rather than the most recent (missing ORDER BY DESC)."""
    src = _read("app/repositories/influencer_collage_repo.py")
    assert "async def get_latest_succeeded(" in src
    # Grab the function body up to the next `async def` for pinning.
    start = src.find("async def get_latest_succeeded(")
    end = src.find("\nasync def ", start + 1)
    body = src[start:end] if end != -1 else src[start:]
    assert "state = 'succeeded'" in body
    assert "generation_date >= CURRENT_DATE - $2::int" in body
    assert "ORDER BY generation_date DESC" in body
    assert "LIMIT 1" in body


def test_orchestrate_calls_fallback_on_failed_and_race_lost():
    """The two brief-mandated hook points — the today-failed branch AND
    the polling-winner-failed branch — must both go through
    _fallback_or_failed. A future refactor that drops either branch
    silently regresses the 2026-07-13 hardening."""
    src = _read("app/services/image_collage.py")
    # Both hook points call the shared helper.
    assert "_fallback_or_failed(pool, bot_id" in src
    # The state='failed' branch in orchestrate + the poll-winner-failed
    # branch in _poll_for_winner both call the helper.
    calls = src.count("await _fallback_or_failed(pool, bot_id")
    assert calls >= 3, (
        f"expected _fallback_or_failed called at ≥3 seams "
        f"(existing-failed + poll-failed + reservation-lost + budget + "
        f"elected-generator-failed); got {calls}"
    )


def test_config_knob_defaults_to_7_days():
    """Rishi's paranoid switch: env COLLAGE_FALLBACK_MAX_DAYS=0 reverts
    to pre-2026-07-13 behavior (no fallback). Default 7 keeps the fix
    active on fresh deploys."""
    src = _read("app/config.py")
    assert 'COLLAGE_FALLBACK_MAX_DAYS = _env_int("COLLAGE_FALLBACK_MAX_DAYS", 7)' in src


def test_dashboard_tile_registered():
    """The ADHD-observability rule — every protective system ships with
    a dashboard signal. A silent drop of the tile would hide the very
    fallback firings the operator needs to notice."""
    src = _read("app/routes/admin_dashboard.py")
    assert "async def _collage_fallback_tile(" in src
    assert "await _collage_fallback_tile(pool)" in src


# ─── behavioural — the four brief-mandated scenarios ────────────────────


class _StubPool:
    """SQL-substring stub covering the two queries the fallback path
    hits: `get_latest_succeeded`'s SELECT and `get`'s SELECT for
    today's row. Both return whatever the test set up."""

    def __init__(
        self,
        *,
        today_row: dict | None = None,
        recent_succeeded: dict | None = None,
    ) -> None:
        self.today_row = today_row
        self.recent_succeeded = recent_succeeded
        # Track what get_latest_succeeded was called with so tests can
        # assert `within_days` propagation.
        self.latest_calls: list[tuple[str, int]] = []

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if (
            "state = 'succeeded'" in sql_norm
            and "ORDER BY generation_date DESC" in sql_norm
        ):
            # get_latest_succeeded(bot_id, within_days)
            self.latest_calls.append((args[0], int(args[1])))
            return self.recent_succeeded
        if "FROM influencer_collages" in sql_norm and "WHERE bot_id = $1" in sql_norm:
            return self.today_row
        # Daily-cost sum for the budget guard etc. — return zero.
        if "SUM(cost_usd)" in sql_norm:
            return {"total": 0.0}
        return None


def _make_row(state: str, gen_date: date, urls: list[str] | None = None) -> dict:
    """Minimal collage row shape that _ready_response can consume."""
    return {
        "id": f"row-{gen_date.isoformat()}",
        "bot_id": "tara",
        "generation_date": gen_date,
        "theme": "Capri beach volleyball",
        "image_urls": urls or [],
        "image_urls_blurred": [],
        "state": state,
        "cost_usd": 0.27,
        "generated_at": (
            datetime.combine(gen_date, datetime.min.time(), tzinfo=timezone.utc)
            if state == "succeeded"
            else None
        ),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _install_pool(monkeypatch, pool):
    """image_collage._fallback_or_failed doesn't reach into config for
    the storage helper, but _ready_response calls storage.generate_
    presigned_url on each image URL. Stub that so the test doesn't
    hit real S3 / raise on invalid keys."""
    from services import image_collage, storage

    monkeypatch.setattr(image_collage, "_today_utc", lambda: TODAY)
    monkeypatch.setattr(storage, "generate_presigned_url", lambda k: f"signed:{k}")


@requires_fastapi
def test_fallback_serves_most_recent_succeeded_when_today_failed(monkeypatch):
    """The load-bearing case: today's row is state='failed'; there IS a
    succeeded row from yesterday. Fallback fires — envelope's status is
    'ready' + carries yesterday's row id + generation_date."""
    from services import image_collage

    yesterday = TODAY - timedelta(days=1)
    pool = _StubPool(
        today_row=_make_row("failed", TODAY),
        recent_succeeded=_make_row(
            "succeeded", yesterday, urls=["collage-clear/tara/y/01.jpg"]
        ),
    )
    _install_pool(monkeypatch, pool)

    result = asyncio.run(
        image_collage.orchestrate(
            pool,
            user_id="u1",
            bot_id="tara",
            theme="beach",
            consume_quota=False,  # skip user_image_requests INSERT
        )
    )
    assert result["status"] == "ready"
    assert result["id"] == f"row-{yesterday.isoformat()}"
    assert result["generation_date"] == yesterday.isoformat()
    assert result["image_urls"] == ["signed:collage-clear/tara/y/01.jpg"]


@requires_fastapi
def test_fallback_no_recent_succeeded_returns_failed(monkeypatch):
    """When there's no succeeded row inside the window (the real
    outage case), the fallback returns None → orchestrate() bubbles
    the actual 'failed' status. Otherwise a multi-day outage would
    silently look healthy."""
    from services import image_collage

    pool = _StubPool(
        today_row=_make_row("failed", TODAY),
        recent_succeeded=None,
    )
    _install_pool(monkeypatch, pool)

    result = asyncio.run(
        image_collage.orchestrate(
            pool,
            user_id="u1",
            bot_id="tara",
            theme="beach",
            consume_quota=False,
        )
    )
    assert result == {"status": "failed", "reason": "generator_failed"}


@requires_fastapi
def test_fallback_window_respects_max_days_config(monkeypatch):
    """The bounded-lookup is the safety valve: setting COLLAGE_FALLBACK_
    MAX_DAYS=0 disables the fallback entirely (paranoid switch). Verify
    that (a) the config value is threaded through to the repo's
    within_days arg AND (b) 0 → no fallback (repo returns None)."""
    from services import image_collage
    import config

    monkeypatch.setattr(config, "COLLAGE_FALLBACK_MAX_DAYS", 0)
    pool = _StubPool(
        today_row=_make_row("failed", TODAY),
        # If the SQL were called at all, this would come back; but at
        # within_days=0 the helper short-circuits without a query.
        recent_succeeded=_make_row("succeeded", TODAY - timedelta(days=1)),
    )
    _install_pool(monkeypatch, pool)

    result = asyncio.run(
        image_collage.orchestrate(
            pool,
            user_id="u1",
            bot_id="tara",
            theme="beach",
            consume_quota=False,
        )
    )
    assert result == {"status": "failed", "reason": "generator_failed"}, (
        "within_days=0 must revert to the pre-2026-07-13 behavior "
        "(no fallback, direct 'failed')"
    )
    # And the SQL was NEVER hit — the repo short-circuits on within_days<=0.
    assert pool.latest_calls == [], (
        f"repo query must short-circuit at within_days=0; got {pool.latest_calls}"
    )


@requires_fastapi
def test_fallback_row_id_is_stable_across_calls_same_day(monkeypatch):
    """Idempotency guard: two calls with the same DB state must return
    THE SAME row (same id + same generation_date). Mobile stores the
    UUID on the message payload for its refetch; a shifting id would
    cause the "chat with me" card to redraw against a different set of
    images per tap."""
    from services import image_collage

    yesterday = TODAY - timedelta(days=1)
    day_before = TODAY - timedelta(days=2)
    fixed_row = _make_row("succeeded", yesterday, urls=["collage-clear/tara/y/01.jpg"])
    pool = _StubPool(
        today_row=_make_row("failed", TODAY),
        recent_succeeded=fixed_row,
    )
    _install_pool(monkeypatch, pool)

    async def _twice():
        r1 = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="tara", theme="beach", consume_quota=False
        )
        r2 = await image_collage.orchestrate(
            pool, user_id="u2", bot_id="tara", theme="beach", consume_quota=False
        )
        return r1, r2

    r1, r2 = asyncio.run(_twice())
    assert r1["status"] == "ready"
    assert r2["status"] == "ready"
    assert r1["id"] == r2["id"], (
        "fallback must return a STABLE row id across calls — mobile refetches "
        "by this UUID + a shift would swap the images under an open chat"
    )
    assert r1["generation_date"] == r2["generation_date"] == yesterday.isoformat()
    # Belt-and-braces: the older day_before row was NOT selected even
    # though it's within the window; ORDER BY generation_date DESC picks
    # the most recent one.
    assert r1["generation_date"] != day_before.isoformat()


# ─── behavioural — elected-generator-failed also falls back ─────────────


@requires_fastapi
def test_elected_generator_failure_also_uses_fallback(monkeypatch):
    """The requester who was elected the generator and whose own batch
    just failed must get the fallback too — otherwise the ONE user who
    tapped Request Images gets 502 while everyone after them (arriving
    to the now-failed row) gets the fallback. Design intent: same UX
    for every requester."""
    from services import image_collage, replicate

    yesterday = TODAY - timedelta(days=1)
    pool = _StubPool(
        today_row=None,  # no row yet → we win the reservation
        recent_succeeded=_make_row(
            "succeeded", yesterday, urls=["collage-clear/tara/y/01.jpg"]
        ),
    )
    _install_pool(monkeypatch, pool)

    # Make the reservation win + the generation "fail" (short batch —
    # matches the design §2.5 content-safety refusal).
    from repositories import influencer_collage_repo

    async def _win_reserve(*a, **k):
        # Populate today_row so subsequent get() calls see it.
        pool.today_row = _make_row("reserved", TODAY)
        return True

    async def _mark_failed(*a, **k):
        pool.today_row = _make_row("failed", TODAY)

    async def _fake_batch(prompt, n, lora_weights_url=None):
        return []  # short = content-safety refusal

    monkeypatch.setattr(influencer_collage_repo, "reserve", _win_reserve)
    monkeypatch.setattr(influencer_collage_repo, "mark_failed", _mark_failed)
    monkeypatch.setattr(replicate, "generate_batch", _fake_batch)

    result = asyncio.run(
        image_collage.orchestrate(
            pool,
            user_id="u1",
            bot_id="tara",
            theme="beach",
            consume_quota=False,
        )
    )
    assert result["status"] == "ready", (
        f"elected-generator-failed requester must get the fallback ready "
        f"envelope, not a raw 'failed' — got {result}"
    )
    assert result["generation_date"] == yesterday.isoformat()


# ─── behavioural — count helper ─────────────────────────────────────────


@requires_fastapi
def test_dashboard_count_helper_handles_db_error(monkeypatch):
    """Every dashboard tile helper degrades-open on DB errors so a
    broken query never breaks the whole dashboard. Pin that
    `count_fallback_serves_last_24h` returns 0 on exception."""
    from repositories import influencer_collage_repo

    class _BrokenPool:
        async def fetchrow(self, sql, *args):
            raise RuntimeError("simulated DB down")

    n = asyncio.run(
        influencer_collage_repo.count_fallback_serves_last_24h(_BrokenPool())
    )
    assert n == 0
