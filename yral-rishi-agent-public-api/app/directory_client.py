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
# READ ORDER (B7 priority — public API surface FIRST):
#   1. list_influencers     — entry point used by GET /api/v1/influencers
#   2. get_influencer       — entry point used by GET /api/v1/influencers/{id}
#   3. init_directory_client / close_directory_client — lifespan hooks
#   4. get_directory_client / _internal_headers       — private helpers
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
# **proposed contract** in DEP-013 — Session 4 ratifies when they
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

# typing.Optional — used for the `Optional[httpx.AsyncClient]`
# annotation on the module-level singleton reference, which is None
# until lifespan startup runs init_directory_client().
from typing import Optional

# httpx — async HTTP client used for the lifespan-managed
# AsyncClient singleton that pools TCP connections to Session 4's
# influencer-and-profile-directory service across the worker's
# lifetime.
import httpx

# get_settings — module-singleton accessor for the pydantic-settings
# model; supplies directory_base_url + the list / by-id path
# templates + the connect/total timeouts the singleton is constructed
# with.
from app.config import get_settings


# Module-level reference set by lifespan startup. None until startup
# completes; raises a RuntimeError on access before startup (loud
# failure beats silent None.get() crash later). Module-level by design:
# httpx.AsyncClient is process-singleton-shaped (one TCP pool per
# worker), not per-request.
_async_client: Optional[httpx.AsyncClient] = None


# Caller name embedded in X-Internal-Caller per the internal-rpc
# contract. Hardcoded here (not in shared-config) because it identifies
# THIS service uniquely. Matches orchestrator_client.INTERNAL_CALLER_NAME
# verbatim — same caller, two destinations.
INTERNAL_CALLER_NAME = "yral-rishi-agent-public-api"


# ===========================================================================
# Public API surface — these are the two functions route handlers call.
# Listed FIRST per B7 priority-order (file header's START HERE points here).
# ===========================================================================


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
          list endpoint. Pagination parameters propagate 1:1 from
          the public-api surface to the internal RPC (per DEP-013
          proposal).
    """
    # Read settings via the lru_cache'd accessor — pydantic-settings
    # parsing happens at most once per process; this call is cheap.
    settings = get_settings()

    # Resolve the lifespan-managed singleton. Raises RuntimeError loudly
    # if startup hasn't completed (better than a NoneType.get() crash
    # inside the route handler's body).
    client = get_directory_client()

    # Issue the GET. `params=` lets httpx encode the query string with
    # URL-safe escaping (no manual f-string concatenation that could
    # silently break on malformed inputs — though pydantic Query
    # validation in the route handler already bounds limit + offset).
    # Headers are the 4-header internal-RPC envelope shared with
    # get_influencer (see _internal_headers).
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
    # Same settings + client resolution pattern as list_influencers.
    settings = get_settings()
    client = get_directory_client()

    # Interpolate the influencer_id into the path template. `.format()`
    # used (not f-string) so the path stays a configurable settings
    # field — operator can override the template via env var without
    # touching this code. The {influencer_id} placeholder name matches
    # the named parameter on the path_template setting so the format
    # call surfaces a clear KeyError if the template gets out of sync.
    path = settings.directory_by_id_path_template.format(influencer_id=influencer_id)

    # Issue the GET. No query string — the id is fully encoded in the
    # path. Same 4-header internal-RPC envelope as list_influencers.
    return await client.get(
        path,
        headers=_internal_headers(user_id=user_id, request_id=request_id),
    )


# ===========================================================================
# Lifespan hooks — called from app/main.py's @asynccontextmanager lifespan.
# ===========================================================================


def init_directory_client() -> None:
    """Construct the singleton httpx.AsyncClient for directory calls.

    WHAT: builds an httpx.AsyncClient pointed at settings.directory_base_url
          with the connect + total timeouts from settings.directory_*.
          Stores on the module-level _async_client reference.
    WHEN: called from app/main.py's lifespan startup phase.
    WHY:  lifespan ownership means ONE pool per worker; closed gracefully
          via close_directory_client() on SIGTERM (mirrors orchestrator).
    """
    # `global` declaration on the module-level singleton write. Marked
    # PLW0603-noqa because module-level state IS intentional here — the
    # pool needs to outlive the function that creates it (it survives
    # for the worker's lifetime).
    global _async_client  # noqa: PLW0603 — module-level singleton is intentional

    # Read settings now (at startup, not lazily) so any malformed env
    # var fails LOUDLY at boot rather than on the first request.
    settings = get_settings()

    # `httpx.Timeout` gives separate connect + read+write+pool dials.
    # Day-8 directive: connect=2s (fail fast on directory-missing),
    # total=5s (catalog reads are non-LLM, non-DB-pool-blocked).
    timeout = httpx.Timeout(
        timeout=settings.directory_request_timeout_seconds,
        connect=settings.directory_connect_timeout_seconds,
    )

    # Construct the client. `base_url` lets the call sites use relative
    # paths (directory_list_path / by_id_path_template); httpx joins
    # them at request time.
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

    # Guard the close call. None means startup didn't fire (test
    # harness, partial-init failure) OR shutdown already ran — both
    # safe to no-op.
    if _async_client is not None:
        # `aclose()` drains the connection pool: rejects new requests,
        # waits for in-flight ones to complete (bounded by the timeout
        # set in init_directory_client), then frees TCP resources.
        await _async_client.aclose()

        # Null the reference so any post-shutdown call raises the
        # RuntimeError in get_directory_client (loud failure beats
        # silent NoneType.get() crash).
        _async_client = None


# ===========================================================================
# Private helpers — read AFTER the public surface + lifespan hooks.
# ===========================================================================


def get_directory_client() -> httpx.AsyncClient:
    """Return the lifespan-managed singleton client.

    WHAT: returns _async_client; raises RuntimeError if lifespan startup
          hasn't run.
    WHEN: called from `list_influencers` / `get_influencer` (and any
          future per-handler caller).
    WHY:  loud failure on misuse beats a silent NoneType.get() crash.
    """
    # The None check + raise is intentional: if startup didn't run (test
    # harness misconfiguration, lifespan import-order bug), every
    # subsequent call gets a clear RuntimeError naming the root cause,
    # not a confusing AttributeError 100 stack frames deep.
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
        # The JWT-validated user_id the public-api auth dep extracted
        # from mobile's Bearer token. Forwarded so the directory can
        # log + (future) per-user rate-limit on the same identifier
        # public-api logged the inbound request against.
        "X-User-Id": user_id,
        # Identifies THIS service to the directory. Hardcoded module-
        # level constant since it's THIS process's identity.
        "X-Internal-Caller": INTERNAL_CALLER_NAME,
        # The cross-service correlation id sourced from public-api's
        # request_id_middleware. Sentry / Langfuse / structured logs
        # all join on this so an on-call can trace one mobile request
        # across the full public-api → directory hop.
        "X-Request-Id": request_id,
        # Same value as X-Request-Id so Langfuse correlates regardless
        # of which header the directory reads. Two headers carry the
        # same value pending coordinator canonicalization (same I6
        # push-back as orchestrator_client carries).
        "X-Trace-Id": request_id,
    }


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
#                              proposed via DEP-013; by-id shape already
#                              declared)
# ===========================================================================
