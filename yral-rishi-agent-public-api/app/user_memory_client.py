# ---------------------------------------------------------------------------
# user_memory_client.py — public-api → Session-5 user-memory-service
# HTTP client.
#
# ⭐ START HERE: one async function, `get_conversation(...)`. Calls
# Session 5's user-memory-service at
# `{user_memory_base_url}{user_memory_get_by_id_path_template}`
# (default `http://yral-rishi-agent-user-memory-service_service:8000/v1/conversations/{conversation_id}`),
# returns the raw httpx.Response so the chat handler can extract the
# `ai_influencer_id` for the per-request orchestrator forwarding.
#
# READ ORDER (B7 priority — public API surface FIRST):
#   1. get_conversation — entry point used by send_message
#   2. init_user_memory_client / close_user_memory_client — lifespan
#      hooks (mirror of orchestrator + directory client patterns)
#   3. get_user_memory_client / _internal_headers — private helpers
#
# WHY MIRROR orchestrator_client.py / directory_client.py SHAPE?
# Three internal HTTP clients now coexist (orchestrator + directory +
# user-memory). Identical shape across all three means one mental
# model + uniform error-mapping in the route handlers. Any future
# refactor that pulls these into a shared `internal_rpc_client`
# helper has three identical-shape call sites to merge.
#
# WHY 4 HEADERS (not 5 like orchestrator)?
# Same reasoning as directory_client: this client only issues GETs
# against the user-memory-service. F10 per-endpoint opt-out for
# stateless reads applies — no `X-Idempotency-Key`. The other 4
# headers (X-User-Id, X-Internal-Caller, X-Request-Id, X-Trace-Id)
# forward identically so cross-service correlation works.
#
# WHY BY-ID (NOT LIST-BY-USER-THEN-FILTER)?
# Session 5's PR #132 (merged 2026-05-23T12:36:58Z) added
# `GET /v1/conversations/{conversation_id}` to user-memory-service.
# The chat handler now goes straight to the by-id endpoint instead
# of the list-then-filter approach (α) the original PR-B2 plan
# would have used.
#
# The by-id endpoint's tenant-isolation contract (load-bearing for
# the trust-boundary mechanism):
#   - Returns 404 for: not-found / soft-deleted / wrong-user
#   - NEVER 403 — refusing to leak the existence of other users'
#     conversations.
# Public-api passes the conversation_id from the URL path + the
# JWT-derived user_id in the X-User-Id header; user-memory rejects
# the lookup if the conversation doesn't belong to this user, and
# public-api translates that 404 into its own 404 envelope — never
# forwards the orchestrator call.
#
# WHY TRUST BOUNDARY MATTERS HERE?
# The `ai_influencer_id` derived from this lookup is THE input PR-B3
# will require the orchestrator to use (no more env fallback). If a
# client could supply `influencer_id` via request body / query string
# / header AND public-api forwarded it without validating against the
# conversation row, a mobile attacker could chat with one influencer
# while billing against another's quota — or worse, exfiltrate
# Soul-File content for an influencer they don't have access to.
# The conversation-lookup is the trust root: public-api derives
# influencer_id ONLY from the conversation record, never from client
# input. The trust-boundary contract test enforces this.
#
# WHY RETRY = 0?
# Same precedent as orchestrator_client + directory_client: retry-on-
# failure adds complexity without value at this stage. user-memory
# GETs are idempotent so a future retry layer is a one-line addition.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# typing.Optional — used for the `Optional[httpx.AsyncClient]`
# annotation on the module-level singleton reference, which is None
# until lifespan startup runs init_user_memory_client().
from typing import Optional

# httpx — async HTTP client used for the lifespan-managed
# AsyncClient singleton that pools TCP connections to Session 5's
# user-memory-service across the worker's lifetime.
import httpx

# get_settings — module-singleton accessor for the pydantic-settings
# model; supplies user_memory_base_url + the by-id path template +
# the connect/total timeouts the singleton is constructed with.
from app.config import get_settings


# Module-level reference set by lifespan startup. None until startup
# completes; raises a RuntimeError on access before startup.
_async_client: Optional[httpx.AsyncClient] = None


# Caller name embedded in X-Internal-Caller per the internal-rpc
# contract. Matches orchestrator_client / directory_client verbatim —
# same caller, three destinations.
INTERNAL_CALLER_NAME = "yral-rishi-agent-public-api"


# ===========================================================================
# Public API surface — the function chat_routes.send_message calls.
# Listed FIRST per B7 priority-order (file header's START HERE points here).
# ===========================================================================


async def get_conversation(
    *,
    user_id: str,
    request_id: str,
    conversation_id: str,
) -> httpx.Response:
    """GET a single conversation from Session 5's user-memory-service.

    WHAT: issues `GET {user_memory_get_by_id_path_template}` with
          `conversation_id` interpolated into the path; returns the
          raw httpx.Response so the chat handler can extract the
          ConversationResponse's `ai_influencer_id` field for
          orchestrator forwarding.
    WHEN: invoked from chat_routes.send_message() on every cache-miss
          turn — public-api looks up the conversation BEFORE calling
          the orchestrator so the per-request `influencer_id` is
          derived from a trusted source (the conversation row), NOT
          from any client-controlled input.
    WHY:  the chat handler needs the conversation's stored
          `ai_influencer_id` to forward into orchestrator_client.
          run_turn(influencer_id=...). PR-B3 will require this
          forwarding (drops the env-var fallback on the orchestrator
          side); PR-B2 ships the trust-boundary derivation.

          The X-User-Id header (set in _internal_headers below) is
          the tenant-isolation key — user-memory returns 404 if the
          conversation doesn't belong to the caller, never 403 (per
          Session 5's contract; refusing to leak existence). The
          chat handler translates that 404 into its own 404
          envelope without forwarding the orchestrator call.
    """
    settings = get_settings()
    client = get_user_memory_client()

    # Interpolate the conversation_id into the path template per the
    # post-PR-#132 contract shape `GET /v1/conversations/{id}`.
    # `.format()` (not f-string) so operator can override the path
    # template via env var without touching this code.
    path = settings.user_memory_get_by_id_path_template.format(
        conversation_id=conversation_id,
    )

    # Issue the GET. No query parameters — the id is in the path.
    # The 4-header internal-RPC envelope carries user_id (for tenant
    # isolation), internal-caller identity, and the two correlation
    # ids.
    return await client.get(
        path,
        headers=_internal_headers(user_id=user_id, request_id=request_id),
    )


# ===========================================================================
# Lifespan hooks — called from app/main.py's @asynccontextmanager lifespan.
# ===========================================================================


def init_user_memory_client() -> None:
    """Construct the singleton httpx.AsyncClient for user-memory calls.

    WHAT: builds an httpx.AsyncClient pointed at
          settings.user_memory_base_url with connect + total timeouts
          from settings.user_memory_*. Stores on the module-level
          _async_client reference.
    WHEN: called from app/main.py's lifespan startup phase.
    WHY:  lifespan ownership means ONE pool per worker; closed
          gracefully via close_user_memory_client() on SIGTERM
          (mirrors orchestrator + directory clients).
    """
    global _async_client  # noqa: PLW0603 — module-level singleton is intentional

    # Read settings now (at startup, not lazily) so any malformed env
    # var fails LOUDLY at boot rather than on the first request.
    settings = get_settings()

    # `httpx.Timeout` gives separate connect + total dials. user-memory
    # is a DB-backed lookup with no LLM compute on the path; same
    # 5s/2s budget as directory_client.
    timeout = httpx.Timeout(
        timeout=settings.user_memory_request_timeout_seconds,
        connect=settings.user_memory_connect_timeout_seconds,
    )

    _async_client = httpx.AsyncClient(
        base_url=settings.user_memory_base_url,
        timeout=timeout,
    )


async def close_user_memory_client() -> None:
    """Close + null the singleton client.

    WHAT: awaits _async_client.aclose() if present + nulls the reference.
    WHEN: lifespan shutdown phase (SIGTERM, Swarm rolling update, etc.).
    WHY:  drains pending user-memory-bound connections cleanly; without
          this, a hung user-memory call could keep the worker alive
          past the shutdown grace period.
    """
    global _async_client  # noqa: PLW0603

    # Guard the close call. None means startup didn't fire OR shutdown
    # already ran — both safe to no-op.
    if _async_client is not None:
        # Drains the pool: rejects new requests, waits for in-flight
        # ones (bounded by the timeout set in init), frees TCP.
        await _async_client.aclose()
        # Null the reference so any post-shutdown call raises the
        # RuntimeError in get_user_memory_client.
        _async_client = None


# ===========================================================================
# Private helpers — read AFTER the public surface + lifespan hooks.
# ===========================================================================


def get_user_memory_client() -> httpx.AsyncClient:
    """Return the lifespan-managed singleton client.

    WHAT: returns _async_client; raises RuntimeError if lifespan
          startup hasn't run.
    WHEN: called from `get_conversation` (and any future per-handler
          caller).
    WHY:  loud failure on misuse beats a silent NoneType.get() crash.
    """
    if _async_client is None:
        raise RuntimeError(
            "user-memory client not initialized — was "
            "init_user_memory_client() called from app lifespan startup?"
        )
    return _async_client


def _internal_headers(*, user_id: str, request_id: str) -> dict[str, str]:
    """Build the 4 internal-call headers shared by every user-memory call.

    WHAT: returns {X-User-Id, X-Internal-Caller, X-Request-Id, X-Trace-Id}.
    WHEN: invoked by every public function in this module that issues
          an httpx request.
    WHY:  single source for the header set; future header-shape change
          touches one line.
    """
    return {
        # The JWT-validated user_id the public-api auth dep extracted
        # from mobile's Bearer token. Forwarded so user-memory-service
        # can scope its query to the caller's conversations (a future
        # ACL layer could reject cross-user reads here).
        "X-User-Id": user_id,
        # Identifies THIS service to user-memory. Hardcoded module-
        # level constant since it's THIS process's identity.
        "X-Internal-Caller": INTERNAL_CALLER_NAME,
        # The cross-service correlation id sourced from public-api's
        # request_id_middleware. Sentry / Langfuse / structured logs
        # all join on this so on-call can trace one mobile request
        # across the public-api → user-memory hop.
        "X-Request-Id": request_id,
        # Same value as X-Request-Id so Langfuse correlates regardless
        # of which header user-memory reads. Two headers carry the
        # same value pending coordinator canonicalization (same I6
        # push-back as orchestrator_client + directory_client carry).
        "X-Trace-Id": request_id,
    }


# ===========================================================================
# RELATED FILES:
#   main.py                  — lifespan startup calls
#                              init_user_memory_client; shutdown calls
#                              close_user_memory_client
#   api/chat_routes.py       — send_message handler invokes this
#                              module's list_conversations_for_user
#                              BEFORE the orchestrator call
#   config.py                — user_memory_base_url /
#                              _get_by_id_path_template /
#                              _request_timeout_seconds /
#                              _connect_timeout_seconds
#   orchestrator_client.py   — mirror-image client for Session 4's
#                              orchestrator (5 headers; mutating POST)
#   directory_client.py      — mirror-image client for Session 4's
#                              influencer-directory (4 headers; GETs)
#   request_id_middleware.py — supplies the X-Request-Id value
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — the contract this client honors
# ===========================================================================
