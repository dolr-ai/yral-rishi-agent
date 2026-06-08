"""Source-level pins for the 2026-06-08 multi-replica cache-drift fix.

The 2-replica swarm + per-replica in-memory cache had a real bug: a
Save on the dashboard refreshed only the replica that handled the
form submit, leaving the other replica's cache stale. This file pins
the fix shape so a future contributor doesn't accidentally regress.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (ROOT / p).read_text()


# ─── pub/sub module exists with the right contract ──────────────────


def test_llm_routing_pubsub_module_exists():
    """The new module is the central piece. It defines the channel name,
    the publish helper, and the subscriber task."""
    src = _read("app/services/llm_routing_pubsub.py")
    assert 'LLM_ROUTING_CHANNEL = "llm_routing_invalidate"' in src
    assert "async def publish_invalidate(" in src
    assert "async def start_subscriber(" in src


def test_pubsub_subscriber_reloads_registry_cache():
    """On every received message, the subscriber must call
    `llm_registry.reload_config_from_db` so the local replica picks up
    whatever the publishing replica wrote."""
    src = _read("app/services/llm_routing_pubsub.py")
    start = src.find("async def start_subscriber(")
    body = src[start:]
    assert "from services import llm_registry" in body
    assert "reload_config_from_db" in body


def test_pubsub_graceful_redis_unavailable():
    """If Redis is unreachable, the subscriber must log + return
    cleanly — NOT crash the app. Mirrors websocket_manager's pattern."""
    src = _read("app/services/llm_routing_pubsub.py")
    sub_start = src.find("async def start_subscriber(")
    body = src[sub_start:]
    assert "if not redis:" in body
    assert "return" in body
    # And publish must also tolerate Redis being unreachable.
    pub_start = src.find("async def publish_invalidate(")
    pub_body = src[pub_start : src.find("async def start_subscriber(")]
    assert "if not redis:" in pub_body
    assert "return False" in pub_body


# ─── registry wires the broadcast into upsert + delete ──────────────


def test_upsert_override_broadcasts_invalidate():
    """Every successful Save on the dashboard must broadcast a
    cache-invalidate message so the other replicas refresh too. Without
    this, the original 2026-06-08 bug returns."""
    src = _read("app/services/llm_registry.py")
    start = src.find("async def upsert_override(")
    end = src.find("\n\n\nasync def delete_override(")
    body = src[start:end]
    assert "_broadcast_invalidate" in body or "publish_invalidate" in body, (
        "upsert_override must broadcast a cache-invalidate after the "
        "local reload — without it, other replicas drift"
    )


def test_delete_override_broadcasts_invalidate():
    """Reset on the dashboard is just as critical to broadcast as Save —
    other replicas need to clear their cache too."""
    src = _read("app/services/llm_registry.py")
    start = src.find("async def delete_override(")
    body = src[start:]
    # Stop reading at the next top-level function.
    next_def = body.find("\n\nasync def ", 1)
    if next_def > 0:
        body = body[:next_def]
    assert "_broadcast_invalidate" in body or "publish_invalidate" in body


def test_broadcast_helper_is_non_fatal_on_redis_error():
    """If Redis is down, a Save should still succeed locally — only the
    cross-replica propagation degrades. Pin that the broadcast helper
    catches and logs, doesn't re-raise."""
    src = _read("app/services/llm_registry.py")
    start = src.find("async def _broadcast_invalidate(")
    end = src.find("\n\n\nasync def upsert_override(")
    body = src[start:end]
    assert "try:" in body
    assert "except Exception" in body


# ─── main.py launches the subscriber ────────────────────────────────


def test_lifespan_starts_llm_routing_pubsub_subscriber():
    """If the subscriber isn't started in the lifespan, the entire fix
    is silent — Save still writes to DB and publishes, but no replica
    listens. Pin the wiring."""
    src = _read("app/main.py")
    assert "llm_routing_pubsub" in src
    assert "start_subscriber" in src
    assert "asyncio.create_task(llm_routing_pubsub.start_subscriber())" in src


# ─── dashboard reload-on-load ───────────────────────────────────────


def test_llm_routing_page_reloads_cache_before_render():
    """Belt-and-suspenders fallback: even if Redis pub/sub is down, the
    dashboard reload-on-GET ensures the operator never sees stale data
    when they hit the page. ~5ms DB hit per page load, negligible."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("async def llm_routing_page(")
    end = src.find("\n\n\n@router.get(\"/admin/llm-routing.json\")")
    body = src[start:end]
    assert "reload_config_from_db" in body, (
        "llm_routing_page must reload cache from DB on every load so a "
        "refresh after Save always shows the correct state"
    )


def test_llm_routing_json_reloads_cache_before_render():
    """Same protection for the machine-consumer JSON endpoint — ops
    scripts shouldn't see stale data either."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("async def llm_routing_json(")
    body = src[start:]
    next_def = body.find("\n\n\nasync def ", 1)
    if next_def > 0:
        body = body[:next_def]
    assert "reload_config_from_db" in body


# ─── Defense-in-depth: dashboard reload errors must NOT 500 ─────────


def test_dashboard_reload_failures_are_caught():
    """The reload-on-load is a best-effort enhancement. If it ever
    raises (e.g. DB transient), the page must still render with the
    last-known cache — not 500. Pin the try/except wrapper."""
    src = _read("app/routes/llm_routing_admin.py")
    for fn in ("llm_routing_page", "llm_routing_json"):
        start = src.find(f"async def {fn}(")
        assert start > 0, f"missing {fn}"
        # Look at the next ~600 chars (full function body).
        body = src[start : start + 800]
        # Each reload call must be wrapped in try/except.
        assert "reload_config_from_db" in body
        assert "try:" in body
        assert "except Exception" in body, (
            f"{fn}: reload_config_from_db must be wrapped in try/except so "
            f"a transient DB blip doesn't 500 the dashboard"
        )
