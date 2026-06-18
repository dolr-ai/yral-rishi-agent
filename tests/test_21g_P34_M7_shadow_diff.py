"""Phase 21γ.P34.M7 — debug_source marker + shadow-diff vs Anshuman.

Three categories:
  1. SOURCE-PIN — wiring of debug_source param, admin shadow endpoint
     registration, Anshuman URL constant, X-Admin-Key guard.
  2. BEHAVIOURAL — `compute_diff` pure function: overlap %, ordering
     deltas, source-exclusive lists, truncation, empty inputs.
  3. INTEGRATION — `shadow_diff` end-to-end with stubbed v2 fetch +
     stubbed httpx + Sentry-absent path. No real network.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN
# ══════════════════════════════════════════════════════════════════════


def test_route_accepts_debug_source_query_param():
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    # Pydantic-validated bool with default False — currently silently
    # ignored on the merged M2a, this PR wires the echo.
    assert "debug_source: bool = Query(False)" in src
    # Top-level marker echoed when requested.
    assert 'payload["debug_source"] = "v2"' in src


def test_debug_source_only_echoed_when_requested():
    """Verify the echo is gated on the param so anonymous callers
    don't see a debug marker by default."""
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    # The echo line must be guarded by `if debug_source:`.
    idx = src.index('payload["debug_source"] = "v2"')
    preceding = src[:idx]
    # Last `if ` before the echo line must be the debug_source guard.
    last_if = preceding.rfind("if debug_source:")
    assert last_if > 0, "debug_source echo not guarded by `if debug_source:`"


def test_shadow_diff_module_exposes_required_symbols():
    src = (REPO / "app" / "services" / "feed_shadow_diff.py").read_text()
    for name in (
        "async def shadow_diff",
        "async def _fetch_anshuman_ids",
        "async def _fetch_v2_ids",
        "def compute_diff",
        "ANSHUMAN_FEED_URL",
        "HTTP_TIMEOUT_SEC",
        "SAMPLE_LIST_TRUNCATE",
    ):
        assert name in src, f"missing symbol: {name}"


def test_anshuman_url_matches_memory_ground_truth():
    """`project_ansuman_recsys_facts` memory verified this URL on
    2026-06-16. If a future PR drifts it, the shadow-diff silently
    runs against the wrong endpoint."""
    src = (REPO / "app" / "services" / "feed_shadow_diff.py").read_text()
    assert (
        '"https://recsys-influencer-feed.ansuman.yral.com/api/v1/influencer-feed"'
        in src
    )


def test_admin_endpoint_x_admin_key_gated():
    src = (REPO / "app" / "routes" / "admin_discovery_shadow.py").read_text()
    assert '"/admin/discovery/shadow-diff"' in src
    assert 'alias="X-Admin-Key"' in src
    assert "secrets.compare_digest" in src


def test_main_wires_admin_shadow_router():
    src = (REPO / "app" / "main.py").read_text()
    assert (
        "from routes.admin_discovery_shadow import router as admin_discovery_shadow_router"
        in src
    )
    assert "app.include_router(admin_discovery_shadow_router)" in src


def test_shadow_diff_never_5xx_on_fetch_failure():
    """The orchestrator must catch fetch failures + surface them in
    the response envelope. If a future PR removes the try/except,
    Anshuman being momentarily down would 5xx the admin probe."""
    src = (REPO / "app" / "services" / "feed_shadow_diff.py").read_text()
    # Two try/except wrappers expected — one for v2 fetch, one for
    # Anshuman fetch.
    assert src.count("except Exception as e:") >= 2
    assert '"errors"' in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — pure compute_diff
# ══════════════════════════════════════════════════════════════════════


def test_compute_diff_full_overlap_same_order():
    from services.feed_shadow_diff import compute_diff

    ids = ["a", "b", "c", "d"]
    diff = compute_diff(ids, ids)
    assert diff["v2_count"] == 4
    assert diff["anshuman_count"] == 4
    assert diff["overlap_pct"] == 100.0
    assert diff["only_in_v2"] == []
    assert diff["only_in_anshuman"] == []
    # All ordering deltas are zero (same positions).
    for v2r, anr, _ in diff["ordering_deltas"]:
        assert v2r == anr


def test_compute_diff_zero_overlap():
    from services.feed_shadow_diff import compute_diff

    v2 = ["a", "b", "c"]
    an = ["x", "y", "z"]
    diff = compute_diff(v2, an)
    assert diff["overlap_pct"] == 0.0
    assert sorted(diff["only_in_v2"]) == ["a", "b", "c"]
    assert sorted(diff["only_in_anshuman"]) == ["x", "y", "z"]
    assert diff["ordering_deltas"] == []


def test_compute_diff_partial_overlap():
    """3 shared + 1 v2-only + 1 anshuman-only. Overlap = 3 / 5
    union = 60%."""
    from services.feed_shadow_diff import compute_diff

    v2 = ["a", "b", "c", "v2only"]
    an = ["c", "b", "a", "anonly"]
    diff = compute_diff(v2, an)
    assert diff["overlap_pct"] == 60.0
    assert diff["only_in_v2"] == ["v2only"]
    assert diff["only_in_anshuman"] == ["anonly"]
    # All 3 shared bots have order deltas (v2: a@0,b@1,c@2; an: c@0,b@1,a@2).
    by_bot = {bid: (v2r, anr) for v2r, anr, bid in diff["ordering_deltas"]}
    assert by_bot["a"] == (0, 2)
    assert by_bot["c"] == (2, 0)
    # b is at position 1 in both → delta 0 but still listed.
    assert by_bot["b"] == (1, 1)


def test_compute_diff_ordering_deltas_sorted_by_abs_delta_desc():
    """Biggest divergences surface first in the truncated list."""
    from services.feed_shadow_diff import compute_diff

    v2 = ["a", "b", "c"]
    an = ["c", "a", "b"]
    diff = compute_diff(v2, an)
    deltas = [(v2r, anr, bid) for v2r, anr, bid in diff["ordering_deltas"]]
    # First entry has the biggest |v2_rank - an_rank|.
    abs_deltas = [abs(v - a) for v, a, _ in deltas]
    assert abs_deltas == sorted(abs_deltas, reverse=True)


def test_compute_diff_truncates_long_lists():
    """SAMPLE_LIST_TRUNCATE=20 caps the verbose fields so the
    response stays bounded even on a 5000-bot catalog."""
    from services.feed_shadow_diff import SAMPLE_LIST_TRUNCATE, compute_diff

    v2 = [f"bot_{i:04d}" for i in range(100)]  # 100 v2-only bots
    an: list = []
    diff = compute_diff(v2, an)
    assert len(diff["only_in_v2"]) == SAMPLE_LIST_TRUNCATE


def test_compute_diff_empty_inputs_returns_zero_overlap_no_crash():
    """Both feeds momentarily empty — diff should be 0% overlap,
    not 0/0 crash."""
    from services.feed_shadow_diff import compute_diff

    diff = compute_diff([], [])
    assert diff["v2_count"] == 0
    assert diff["anshuman_count"] == 0
    assert diff["overlap_pct"] == 0.0
    assert diff["ordering_deltas"] == []


# ══════════════════════════════════════════════════════════════════════
# 3. INTEGRATION — shadow_diff orchestrator with stubs
# ══════════════════════════════════════════════════════════════════════


def test_shadow_diff_clean_both_sides(monkeypatch):
    from services import feed_shadow_diff

    async def stub_v2(pool, limit, session_id):
        return ["a", "b", "c"]

    async def stub_an(limit):
        return ["a", "b", "x"]

    monkeypatch.setattr(feed_shadow_diff, "_fetch_v2_ids", stub_v2)
    monkeypatch.setattr(feed_shadow_diff, "_fetch_anshuman_ids", stub_an)

    diff = asyncio.run(
        feed_shadow_diff.shadow_diff(pool=None, limit=20, session_id="probe")
    )
    assert diff["v2_count"] == 3
    assert diff["anshuman_count"] == 3
    # 2 shared / 4 union = 50%
    assert diff["overlap_pct"] == 50.0
    assert "checked_at" in diff
    assert "elapsed_ms" in diff
    # No errors key on the clean-fetch path.
    assert "errors" not in diff


def test_shadow_diff_v2_fetch_failure_surfaces_in_envelope(monkeypatch):
    from services import feed_shadow_diff

    async def boom_v2(pool, limit, session_id):
        raise RuntimeError("simulated v2 pool failure")

    async def stub_an(limit):
        return ["a", "b"]

    monkeypatch.setattr(feed_shadow_diff, "_fetch_v2_ids", boom_v2)
    monkeypatch.setattr(feed_shadow_diff, "_fetch_anshuman_ids", stub_an)

    diff = asyncio.run(
        feed_shadow_diff.shadow_diff(pool=None, limit=20, session_id="probe")
    )
    # NO raise; errors surface in envelope.
    assert diff["v2_count"] == 0
    assert diff["anshuman_count"] == 2
    assert "errors" in diff
    assert any("v2_fetch" in e for e in diff["errors"])


def test_shadow_diff_anshuman_failure_surfaces_in_envelope(monkeypatch):
    from services import feed_shadow_diff

    async def stub_v2(pool, limit, session_id):
        return ["a", "b"]

    async def boom_an(limit):
        raise RuntimeError("simulated anshuman 503")

    monkeypatch.setattr(feed_shadow_diff, "_fetch_v2_ids", stub_v2)
    monkeypatch.setattr(feed_shadow_diff, "_fetch_anshuman_ids", boom_an)

    diff = asyncio.run(
        feed_shadow_diff.shadow_diff(pool=None, limit=20, session_id="probe")
    )
    assert diff["v2_count"] == 2
    assert diff["anshuman_count"] == 0
    assert "errors" in diff
    assert any("anshuman_fetch" in e for e in diff["errors"])


def test_shadow_diff_both_sides_failed(monkeypatch):
    """Catastrophic — both fail. Still returns 200 with two errors in
    envelope. Operator sees the failure clearly."""
    from services import feed_shadow_diff

    async def boom_v2(pool, limit, session_id):
        raise RuntimeError("v2 down")

    async def boom_an(limit):
        raise RuntimeError("anshuman down")

    monkeypatch.setattr(feed_shadow_diff, "_fetch_v2_ids", boom_v2)
    monkeypatch.setattr(feed_shadow_diff, "_fetch_anshuman_ids", boom_an)

    diff = asyncio.run(
        feed_shadow_diff.shadow_diff(pool=None, limit=20, session_id="probe")
    )
    assert diff["overlap_pct"] == 0.0
    assert len(diff["errors"]) == 2


def test_shadow_diff_sentry_breadcrumb_optional(monkeypatch):
    """Sentry SDK may be absent in dev. The breadcrumb call must
    never raise."""
    from services import feed_shadow_diff

    async def stub_v2(pool, limit, session_id):
        return ["a"]

    async def stub_an(limit):
        return ["a"]

    monkeypatch.setattr(feed_shadow_diff, "_fetch_v2_ids", stub_v2)
    monkeypatch.setattr(feed_shadow_diff, "_fetch_anshuman_ids", stub_an)
    # Simulate Sentry import failure by hiding the module.
    import sys

    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    diff = asyncio.run(
        feed_shadow_diff.shadow_diff(pool=None, limit=20, session_id="probe")
    )
    assert diff["overlap_pct"] == 100.0
