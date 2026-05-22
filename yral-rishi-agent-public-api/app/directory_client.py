# ---------------------------------------------------------------------------
# directory_client.py — public-api → Session-4 influencer-and-profile-directory
# HTTP client.
#
# ⭐ START HERE: two async functions, `list_influencers(...)` and
# `get_influencer(...)`. Both post to Session 4's directory at
# `{directory_base_url}{directory_list_path}` (default
# `http://yral-rishi-agent-influencer-and-profile-directory_service:8000/v1/influencers`).
# Both forward the 4 internal-call headers and return the directory's
# httpx.Response unparsed so the route handlers can wrap in an
# ApiResponse envelope on success or map status codes on failure.
#
# WHY MIRROR orchestrator_client.py VERBATIM?
# Both clients are public-api → Session-4-service HTTP gateways with the
# same wire-shape concerns (lifespan-managed pool, internal-call
# headers, failure-mode mapping). Identical structure means one mental
# model for both + uniform error-mapping in the route handlers. Any
# future refactor that pulls these into a shared "internal-rpc-client"
# helper has two identical-shape call sites to merge, not two
# divergent ones.
#
# WHY ONLY 4 HEADERS (not 5 like orchestrator)?
# orchestrator_client.py forwards 5: X-User-Id, X-Idempotency-Key,
# X-Request-Id, X-Internal-Caller, X-Trace-Id. We drop X-Idempotency-Key
# here because directory calls are GETs — no state mutation, no dedup
# needed (F10's per-endpoint-opt-out carve-out for stateless reads
# applies). The other 4 forward identically so logs / Sentry / Langfuse
# correlate across the same request_id.
#
# WHY ai_influencer_id IS A PATH PARAMETER (not a body field)?
# Per interface-contracts/01-internal-rpc-contracts.md the
# directory's by-id endpoint is `GET /influencers/{id}`. The list
# endpoint shape (`GET /v1/influencers?limit&offset`) is the
# **proposed contract** in DEP-012 — Session 4 ratifies when they
# build the real endpoint, or pushes back with a different shape.
#
# WHY RETRY = 0 (not 3 with backoff)?
# Same precedent as orchestrator_client: retry-on-failure adds
# complexity without value at this stage. Directory GETs are
# idempotent so a future retry layer is a one-line addition; not
# adding it now is A2.1 single-concern.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Optional

import httpx

from app.config import get_settings


# Module-level reference set by lifespan startup. None until startup
# completes; raises a RuntimeError on access before startup (loud
# failure beats silent None.get() crash later).
_async_client: Optional[httpx.AsyncClient] = None


# Caller name embedded in X-Internal-Caller per the internal-rpc
# contract. Hardcoded here (not in shared-config) because it identifies
# THIS service uniquely. Matches orchestrator_client.INTERNAL_CALLER_NAME
# verbatim — same caller, two destinations.
INTERNAL_CALLER_NAME = "yral-rishi-agent-public-api"


def init_directory_client() -> None:
    """Construct the singleton httpx.AsyncClient for directory calls.

    WHAT: builds an httpx.AsyncClient pointed at settings.directory_base_url
          with the connect + total timeouts from settings.directory_*.
          Stores on the module-level _async_client reference.
    WHEN: called from app/main.py's lifespan startup phase.
    WHY:  lifespan ownership means ONE pool per worker; closed gracefully
          via close_directory_client() on SIGTERM (mirrors orchestrator).
    """
    global _async_client  # noqa: PLW0603 — module-level singleton is intentional
    settings = get_settings()
    timeout = httpx.Timeout(
        timeout=settings.directory_request_timeout_seconds,
        connect=settings.directory_connect_timeout_seconds,
    )
    _async_client = httpx.AsyncClient(
        base_url=settings.directory_base_url,
        timeout=timeout,
    )


async def close_directory_client() -> None:
    """Close + null the singleton client.

    WHAT: awaits _async_client.aclose() if present + nulls the reference.
    WHEN: lifespan shutdown phase (SIGTERM, Swarm rolling update, etc.).
    WHY:  drains pending directory-bound connections cleanly; without
          this, a hung directory call could keep the worker alive past
          the shutdown grace period.
    """
    global _async_client  # noqa: PLW0603
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


def get_directory_client() -> httpx.AsyncClient:
    """Return the lifespan-managed singleton client.

    WHAT: returns _async_client; raises RuntimeError if lifespan startup
          hasn't run.
    WHEN: called from `list_influencers` / `get_influencer` (and any
          future per-handler caller).
    WHY:  loud failure on misuse beats a silent NoneType.get() crash.
    """
    if _async_client is None:
        raise RuntimeError(
            "directory client not initialized — was init_directory_client() "
            "called from app lifespan startup?"
        )
    return _async_client


def _internal_headers(*, user_id: str, request_id: str) -> dict[str, str]:
    """Build the 4 internal-call headers shared by every directory call.

    WHAT: returns {X-User-Id, X-Internal-Caller, X-Request-Id, X-Trace-Id}.
    WHEN: invoked by every public function in this module that issues
          an httpx request.
    WHY:  single source for the header set so list + by-id stay in
          lockstep + a future header-shape change touches one line.
    """
    return {
        "X-User-Id": user_id,
        "X-Internal-Caller": INTERNAL_CALLER_NAME,
        "X-Request-Id": request_id,
        # Same value as X-Request-Id so Langfuse correlates regardless
        # of which header the directory reads. Two headers carry the
        # same value pending coordinator canonicalization (same I6
        # push-back as orchestrator_client carries).
        "X-Trace-Id": request_id,
    }


async def list_influencers(
    *,
    user_id: str,
    request_id: str,
    limit: int,
    offset: int,
) -> httpx.Response:
    """GET the influencer list from Session 4's directory.

    WHAT: issues `GET {directory_list_path}?limit=N&offset=N` against
          the singleton client; returns the raw httpx.Response so the
          route handler can interpret status + JSON body.
    WHEN: invoked from influencer_routes.list_influencers() on every
          /api/v1/influencers request after auth + flag-gate pass.
    WHY:  single place that knows the directory's wire shape for the
          list endpoint. Pagination params propagate 1:1 from the
          public-api surface to the internal RPC (per DEP-012 proposal).
    """
    settings = get_settings()
    client = get_directory_client()
    return await client.get(
        settings.directory_list_path,
        params={"limit": limit, "offset": offset},
        headers=_internal_headers(user_id=user_id, request_id=request_id),
    )


async def get_influencer(
    *,
    user_id: str,
    request_id: str,
    influencer_id: str,
) -> httpx.Response:
    """GET a single influencer from Session 4's directory.

    WHAT: issues `GET {directory_by_id_path_template}` with influencer_id
          interpolated into the path; returns the raw httpx.Response so
          the route handler can interpret status + JSON body.
    WHEN: invoked from influencer_routes.get_influencer() on every
          /api/v1/influencers/{id} request after auth + flag-gate pass.
    WHY:  single place that knows the directory's wire shape for the
          by-id endpoint per
          interface-contracts/01-internal-rpc-contracts.md.
    """
    settings = get_settings()
    client = get_directory_client()
    path = settings.directory_by_id_path_template.format(influencer_id=influencer_id)
    return await client.get(
        path,
        headers=_internal_headers(user_id=user_id, request_id=request_id),
    )


# ===========================================================================
# RELATED FILES:
#   main.py                  — lifespan startup calls init_directory_client;
#                              shutdown calls close_directory_client
#   api/influencer_routes.py — list_influencers / get_influencer handlers
#                              invoke this module's functions
#   config.py                — directory_base_url / _list_path /
#                              _by_id_path_template / _request_timeout_seconds /
#                              _connect_timeout_seconds
#   orchestrator_client.py   — mirror-image client for Session 4's
#                              orchestrator; same shape, 5 headers vs
#                              this module's 4 (no X-Idempotency-Key
#                              for stateless GETs)
#   request_id_middleware.py — supplies the X-Request-Id value
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — the contract this client honors (list shape
#                              proposed via DEP-012; by-id shape already
#                              declared)
# ===========================================================================
