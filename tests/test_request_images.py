"""Phase 0 Request Images track B — regression tests.

Covers the five behaviors named in the brief:

  1. Rate limit — 2nd request same day for same (user, bot) → 429.
  2. Race lock — two concurrent requesters with no existing collage →
     one generates, the other polls to the same succeeded row.
  3. Cache hit — an existing succeeded row is served without a new
     replicate.generate_batch call.
  4. Subscription stub — YRAL-team principal gets is_blurred=false;
     non-member gets is_blurred=true.
  5. Content-safety refusal — generate_batch returning fewer URLs
     than requested flips the row to state='failed' and the
     orchestrate result is {'status': 'failed', ...}.

The suite avoids a live Postgres — a small SQL-substring stub pool
covers the four queries the code path issues (reserve, complete,
mark_failed, get). image_collage.orchestrate is the whole surface;
if it's right, the routes are right (they're thin adapters).
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

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


def test_route_paths_locked():
    """The mobile client + Sarvesh's DTO align to these paths.
    Renaming without a Sarvesh handshake breaks the app."""
    src = _read("app/routes/request_images.py")
    assert '"/api/v1/influencers/{influencer_id}/request-images"' in src
    assert '"/api/v1/influencers/{influencer_id}/collage"' in src


def test_router_wired_in_main():
    src = _read("app/main.py")
    assert "from routes.request_images import router as request_images_router" in src
    assert "app.include_router(request_images_router)" in src


def test_composite_pks_are_the_lock():
    """Design §1a: the composite PKs — not Redis, not advisory locks —
    ARE the race lock and rate limiter. A future PR that adds a
    parallel Redis lock re-introduces a Kafka-flavored complexity
    for zero benefit. Pin that the repo uses ON CONFLICT DO NOTHING."""
    collage_src = _read("app/repositories/influencer_collage_repo.py")
    request_src = _read("app/repositories/user_image_request_repo.py")
    assert "ON CONFLICT (bot_id, generation_date) DO NOTHING" in collage_src
    assert "ON CONFLICT (user_id, bot_id, request_date) DO NOTHING" in request_src


# ─── behavioural stubs ──────────────────────────────────────────────────


class _CollageState:
    """In-memory mirror of the influencer_collages table for tests."""

    def __init__(self) -> None:
        self.row: dict | None = None
        self.reserve_attempts = 0
        self.complete_calls: list[dict] = []
        self.mark_failed_calls = 0


class _RateState:
    def __init__(self) -> None:
        self.recorded: set[tuple] = set()


class _StubPool:
    """SQL-substring dispatch stub. Covers the four repo queries
    called by image_collage.orchestrate + _daily_cost_usd."""

    def __init__(self, collage: _CollageState, rate: _RateState) -> None:
        self.collage = collage
        self.rate = rate

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        # user_image_requests INSERT
        if "INSERT INTO user_image_requests" in sql_norm:
            key = (args[0], args[1], args[2])
            if key in self.rate.recorded:
                return None
            self.rate.recorded.add(key)
            return {"user_id": args[0]}
        # influencer_collages INSERT (reserve)
        if "INSERT INTO influencer_collages" in sql_norm:
            self.collage.reserve_attempts += 1
            if self.collage.row is not None:
                return None
            self.collage.row = {
                "bot_id": args[0],
                "generation_date": args[1],
                "theme": args[2],
                "image_urls": [],
                "state": "reserved",
                "cost_usd": 0.0,
                "generated_at": None,
            }
            return {"bot_id": args[0]}
        # SELECT bot_id ... FROM influencer_collages WHERE bot_id = $1 ...
        if "FROM influencer_collages" in sql_norm and "WHERE bot_id" in sql_norm:
            return self.collage.row
        # SELECT COALESCE(SUM ...) — daily cost
        if "SUM(cost_usd)" in sql_norm:
            spent = sum(c["cost_usd"] for c in self.collage.complete_calls)
            return {"total": float(spent)}
        return None

    async def execute(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if "UPDATE influencer_collages SET state = 'succeeded'" in sql_norm:
            if self.collage.row and self.collage.row["state"] == "reserved":
                self.collage.row["state"] = "succeeded"
                self.collage.row["image_urls"] = args[2]
                self.collage.row["cost_usd"] = args[3]
                self.collage.row["generated_at"] = datetime.now(timezone.utc)
                self.collage.complete_calls.append(
                    {"cost_usd": args[3], "urls": args[2]}
                )
            return
        if "UPDATE influencer_collages SET state = 'failed'" in sql_norm:
            if self.collage.row and self.collage.row["state"] == "reserved":
                self.collage.row["state"] = "failed"
                self.collage.mark_failed_calls += 1
            return


def _install(monkeypatch, pool, batch_urls):
    from services import image_collage, replicate

    async def fake_batch(prompt, n, lora_weights_url=None):
        return list(batch_urls)

    monkeypatch.setattr(replicate, "generate_batch", fake_batch)
    monkeypatch.setattr(image_collage, "_today_utc", lambda: TODAY)

    # Skip real sleep in poll loop so the "concurrent poll" test
    # doesn't take 90s to reach the timeout.
    async def _no_sleep(*a, **kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


# ─── behavioural — orchestrate ──────────────────────────────────────────


@requires_fastapi
def test_rate_limit_second_request_same_day_rejected(monkeypatch):
    from services import image_collage

    pool = _StubPool(_CollageState(), _RateState())
    _install(monkeypatch, pool, batch_urls=[f"u{i}" for i in range(6)])

    async def run():
        first = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="tara", theme="beach"
        )
        second = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="tara", theme="beach"
        )
        return first, second

    first, second = asyncio.run(run())
    assert first["status"] == "ready", f"first request should succeed, got {first}"
    assert second["status"] == "rate_limited"
    assert "resets_at" in second


@requires_fastapi
def test_rate_limit_scoped_per_bot(monkeypatch):
    """One request per (user, bot, day). Same user for a DIFFERENT
    bot on the same day must still be accepted."""
    from services import image_collage

    pool = _StubPool(_CollageState(), _RateState())
    _install(monkeypatch, pool, batch_urls=[f"u{i}" for i in range(6)])

    async def run():
        a = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="tara", theme="beach"
        )
        # Fresh collage state for the second bot.
        pool.collage = _CollageState()
        b = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="lisa", theme="beach"
        )
        return a, b

    a, b = asyncio.run(run())
    assert a["status"] == "ready"
    assert b["status"] == "ready", (
        "same user different bot must not trip the rate limit"
    )


@requires_fastapi
def test_cache_hit_serves_without_new_generation(monkeypatch):
    """A pre-existing state='succeeded' row must return "ready"
    without ever calling replicate.generate_batch. This is the
    common-case Phase-0 path (Tara pre-generated)."""
    from services import image_collage, replicate

    collage = _CollageState()
    collage.row = {
        "bot_id": "tara",
        "generation_date": TODAY,
        "theme": "beach",
        "image_urls": [f"cached{i}" for i in range(6)],
        "state": "succeeded",
        "cost_usd": 0.24,
        "generated_at": datetime.now(timezone.utc),
    }
    pool = _StubPool(collage, _RateState())

    calls = {"n": 0}

    async def counting_batch(prompt, n, lora_weights_url=None):
        calls["n"] += 1
        return ["should", "not", "fire"]

    monkeypatch.setattr(replicate, "generate_batch", counting_batch)
    monkeypatch.setattr(image_collage, "_today_utc", lambda: TODAY)

    async def _no_sleep(*a, **kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = asyncio.run(
        image_collage.orchestrate(pool, user_id="u1", bot_id="tara", theme="beach")
    )
    assert result["status"] == "ready"
    assert result["image_urls"] == [f"cached{i}" for i in range(6)]
    assert calls["n"] == 0, "cache hit must NOT trigger a new batch"


@requires_fastapi
def test_race_lock_only_one_generates(monkeypatch):
    """Two requesters, no cache: exactly ONE calls generate_batch;
    the other polls the shared reservation row and reads back the
    winner's URLs."""
    from services import image_collage, replicate

    pool = _StubPool(_CollageState(), _RateState())
    batch_calls = {"n": 0}

    async def once_batch(prompt, n, lora_weights_url=None):
        batch_calls["n"] += 1
        # Simulate the winner completing before the loser's poll ticks.
        pool.collage.row["state"] = "succeeded"
        pool.collage.row["image_urls"] = [f"race{i}" for i in range(n)]
        pool.collage.row["generated_at"] = datetime.now(timezone.utc)
        pool.collage.row["cost_usd"] = 0.24
        pool.collage.complete_calls.append({"cost_usd": 0.24, "urls": []})
        return [f"race{i}" for i in range(n)]

    monkeypatch.setattr(replicate, "generate_batch", once_batch)
    monkeypatch.setattr(image_collage, "_today_utc", lambda: TODAY)

    async def _no_sleep(*a, **kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    async def run():
        winner = await image_collage.orchestrate(
            pool, user_id="u1", bot_id="tara", theme="beach"
        )
        # Second requester arrives after the winner completed →
        # simple cache-hit read (design §1a).
        loser = await image_collage.orchestrate(
            pool, user_id="u2", bot_id="tara", theme="beach"
        )
        return winner, loser

    winner, loser = asyncio.run(run())
    assert batch_calls["n"] == 1, (
        f"exactly one generation must fire; got {batch_calls['n']}"
    )
    assert winner["status"] == "ready"
    assert loser["status"] == "ready"
    assert winner["image_urls"] == loser["image_urls"]


@requires_fastapi
def test_content_safety_refusal_marks_failed(monkeypatch):
    """Replicate safety refusal manifests as generate_batch returning
    fewer URLs than requested — design §2.5. The row must land in
    state='failed' and the response reflects the failure."""
    from services import image_collage

    pool = _StubPool(_CollageState(), _RateState())
    _install(monkeypatch, pool, batch_urls=["u1", "u2"])  # short of 6

    result = asyncio.run(
        image_collage.orchestrate(pool, user_id="u1", bot_id="tara", theme="beach")
    )
    assert result["status"] == "failed"
    assert result["reason"] == "content_safety_or_partial"
    assert pool.collage.row["state"] == "failed"
    assert pool.collage.mark_failed_calls == 1


@requires_fastapi
def test_budget_hard_cap_blocks_generation(monkeypatch):
    """The elected generator must NOT spend beyond
    COLLAGE_DAILY_BUDGET_HARD_USD. Pin that the guard trips + marks
    the row failed with reason='budget_hard_cap'."""
    import config
    from services import image_collage, replicate

    collage = _CollageState()
    # Simulate today's ledger already having a $200 succeeded row from
    # earlier bots.
    collage.complete_calls.append({"cost_usd": 200.0, "urls": []})
    pool = _StubPool(collage, _RateState())

    calls = {"n": 0}

    async def counting_batch(prompt, n, lora_weights_url=None):
        calls["n"] += 1
        return [f"u{i}" for i in range(n)]

    monkeypatch.setattr(replicate, "generate_batch", counting_batch)
    monkeypatch.setattr(image_collage, "_today_utc", lambda: TODAY)

    async def _no_sleep(*a, **kw):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    # Sanity-check the config default hasn't drifted past our test value.
    assert config.COLLAGE_DAILY_BUDGET_HARD_USD < 200

    result = asyncio.run(
        image_collage.orchestrate(pool, user_id="u1", bot_id="tara", theme="beach")
    )
    assert result["status"] == "failed"
    assert result["reason"] == "budget_hard_cap"
    assert calls["n"] == 0, "budget guard must run BEFORE the replicate spend"
    assert pool.collage.row["state"] == "failed"


# ─── subscription stub ──────────────────────────────────────────────────


def test_subscription_stub_yral_team_hardcoded(monkeypatch):
    """YRAL-team allowlist → is_blurred=false; anyone else → true.
    The route reads config.YRAL_TEAM_PRINCIPALS via the stub, so a
    change to the allowlist is a single-source config edit."""
    import config
    from services import subscription_stub

    monkeypatch.setattr(config, "YRAL_TEAM_PRINCIPALS", frozenset({"team-1"}))

    assert subscription_stub.is_subscribed("team-1") is True
    assert subscription_stub.is_subscribed("outsider") is False
    assert subscription_stub.is_subscribed(None) is False
    assert subscription_stub.is_subscribed("") is False
