"""Phase 21γ.P34.M0 — Discovery Feed admin pins.

Source-pin tests that defend the wiring + the contract. Real DB
behaviour is left to the migrations-CI job + the FastAPI integration
suite (TestClient hits aren't easy here without a DB). The contract
this file pins:

  - migration 041 creates `trending_overrides` with the exact columns
    M2's composer will JOIN on.
  - the admin endpoints exist + are gated on X-Admin-Key.
  - the routes are wired into main.py.
  - the admin dashboard surfaces a pins tile.

If any of these drift, M2 (next milestone) will silently fail.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]

# The import-smoke tests need pydantic + fastapi installed (i.e. a CI
# env with requirements.txt). Locally on a bare interpreter we just
# skip them; the source-pin tests above already defend the wiring.
try:
    import pydantic  # noqa: F401
    import fastapi  # noqa: F401

    _RUNTIME_AVAILABLE = True
except ImportError:
    _RUNTIME_AVAILABLE = False

requires_runtime = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="pydantic/fastapi not installed (local dev); covered by CI",
)


# ─── migration 041 shape ────────────────────────────────────────────────


def test_migration_041_creates_trending_overrides():
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS trending_overrides" in src


def test_migration_041_required_columns_present():
    """M2 composer JOIN depends on these column names. If a future PR
    renames any of them, this test catches it before the composer
    starts returning empty pins silently."""
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    for col in (
        "influencer_id",
        "pinned_rank",
        "note",
        "expires_at",
        "created_by",
        "created_at",
        "updated_at",
    ):
        assert col in src, f"missing column: {col}"


def test_migration_041_fk_to_ai_influencers():
    """FK ON DELETE CASCADE — when an influencer is hard-deleted the
    pin disappears too. (Soft-deletes via is_active won't trigger
    this; that's a separate concern handled by the M2 composer's
    `WHERE is_active='active'` filter.)"""
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    assert "REFERENCES ai_influencers(id)" in src
    assert "ON DELETE CASCADE" in src


def test_migration_041_rank_check_constraint():
    """Bounded rank prevents an operator from accidentally pinning at
    rank=99999 + breaking the composer's slot reservation logic."""
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    assert "CHECK (pinned_rank >= 1 AND pinned_rank <= 1000)" in src


def test_migration_041_indexes_for_composer_path():
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    assert "idx_trending_overrides_rank" in src
    assert "idx_trending_overrides_expires" in src
    # Expiry index must be partial so the table doesn't carry an index
    # entry per permanent pin (most pins are permanent).
    assert "WHERE expires_at IS NOT NULL" in src


def test_migration_041_has_squawk_preamble():
    src = (REPO / "migrations" / "041_trending_overrides.sql").read_text()
    assert "SET lock_timeout = '3s';" in src
    assert "SET statement_timeout = '60s';" in src


# ─── admin_discovery route shape ────────────────────────────────────────


def test_admin_discovery_module_exposes_three_endpoints():
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert '"/api/v2/admin/discovery/pin"' in src
    assert '"/api/v2/admin/discovery/unpin"' in src
    assert '"/api/v2/admin/discovery/pins"' in src
    # Method shapes
    assert '@router.post("/api/v2/admin/discovery/pin")' in src
    assert '@router.post("/api/v2/admin/discovery/unpin")' in src
    assert '@router.get("/api/v2/admin/discovery/pins")' in src


def test_admin_discovery_uses_x_admin_key_with_constant_time_compare():
    """Same auth pattern as /admin/influencers/{id}/ban — defends
    against timing attacks via secrets.compare_digest."""
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert 'alias="X-Admin-Key"' in src
    assert "secrets.compare_digest" in src
    assert "config.ADMIN_KEY" in src


def test_admin_discovery_pin_validates_rank_bounds():
    """Pydantic gates rank at 1..1000 before the SQL even runs. Saves
    a round-trip on bad input + gives the caller a clear 422."""
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert "pinned_rank: int = Field(..., ge=1, le=1000)" in src


def test_admin_discovery_upsert_semantics():
    """Re-pinning the same influencer MUST update in place (rank
    change, note change, expiry extension) rather than insert-fail.
    M0 is the operator-facing knob; idempotence matters more than
    strict insert semantics."""
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert "ON CONFLICT (influencer_id) DO UPDATE SET" in src


def test_admin_discovery_unpin_no_op_on_missing():
    """Unpinning a non-pinned influencer returns 200 with
    `deleted: false` — operator can call /unpin defensively without
    needing to check /pins first."""
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert "deleted = await _delete_pin" in src
    assert "'deleted': deleted" in src or '"deleted": deleted' in src


def test_admin_discovery_list_joins_display_name():
    """GET /pins surfaces the display_name so the operator UI doesn't
    have to round-trip /influencers/{id} per row."""
    src = (REPO / "app" / "routes" / "admin_discovery.py").read_text()
    assert "LEFT JOIN ai_influencers" in src
    assert "display_name AS influencer_display_name" in src


# ─── main.py wiring ─────────────────────────────────────────────────────


def test_main_wires_admin_discovery_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "from routes.admin_discovery import router as admin_discovery_router" in src
    assert "app.include_router(admin_discovery_router)" in src


# ─── dashboard tile ─────────────────────────────────────────────────────


def test_dashboard_renders_discovery_pins_tile():
    src = (REPO / "app" / "routes" / "admin_dashboard.py").read_text()
    assert "_discovery_pins_tile" in src
    assert "await _discovery_pins_tile(pool)" in src
    assert "Discovery pins (Phase 21γ.P34.M0)" in src


def test_dashboard_tile_falls_back_gracefully_pre_migration():
    """Before migration 041 is applied, the tile must render an
    "off" state (not crash the dashboard). Mirrors the pattern other
    tiles use for not-yet-shipped systems."""
    src = (REPO / "app" / "routes" / "admin_dashboard.py").read_text()
    # The tile uses try/except around the SQL with a graceful fallback
    assert "except Exception as e" in src
    assert '"table not yet applied"' in src


# ─── module import smoke (catches name typos before runtime) ────────────


@requires_runtime
def test_admin_discovery_module_imports_cleanly():
    from routes import admin_discovery

    assert admin_discovery.router is not None
    # Pydantic models exist + validate
    from routes.admin_discovery import PinRequest, UnpinRequest

    p = PinRequest(influencer_id="abc", pinned_rank=1)
    assert p.pinned_rank == 1
    u = UnpinRequest(influencer_id="abc")
    assert u.influencer_id == "abc"


@requires_runtime
def test_pin_request_rejects_out_of_range_rank():
    from pydantic import ValidationError

    from routes.admin_discovery import PinRequest

    for bad in (0, -1, 1001, 9999):
        try:
            PinRequest(influencer_id="abc", pinned_rank=bad)
            raise AssertionError(f"should have rejected rank={bad}")
        except ValidationError:
            pass


@requires_runtime
def test_pin_request_rejects_empty_influencer_id():
    from pydantic import ValidationError

    from routes.admin_discovery import PinRequest

    try:
        PinRequest(influencer_id="", pinned_rank=1)
        raise AssertionError("should have rejected empty id")
    except ValidationError:
        pass
