# ---------------------------------------------------------------------------
# orchestrator_client.py — public-api → Session-4 orchestrator HTTP client.
#
# ⭐ START HERE: one async function, `run_turn(...)`. Posts a chat-turn
# request to Session 4's orchestrator at
# `{orchestrator_base_url}{orchestrator_run_turn_path}` (default
# `http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn`),
# forwards the 5 internal-call headers, returns the orchestrator's
# JSON response unparsed for the chat handler to wrap in an
# ApiResponse envelope.
#
# WHY A LIFESPAN-MANAGED SINGLETON (vs per-request httpx.AsyncClient)?
# httpx.AsyncClient pools TCP connections to the same host. Per-request
# construction would re-handshake every call (10-30 ms RTT on the
# overlay) AND prevent HTTP/2 multiplexing. Lifespan ownership means
# ONE connection pool per worker, reused across requests, gracefully
# drained on SIGTERM (per H1).
#
# WHY app/main.py OWNS THE LIFESPAN STARTUP / SHUTDOWN?
# FastAPI's `@asynccontextmanager lifespan` runs once per process
# lifetime — perfect place to allocate + release the singleton. The
# handler gets the client via `get_orchestrator_client()` which reads
# the module-level reference set in lifespan startup.
#
# WHY RETRY = 0 (not 3 with backoff)?
# Per the Day-4C directive: "Retry: 0 — idempotency layer handles dedup;
# retry-on-failure adds complexity without value at Day 4." A future
# Day-N PR that adds retries should ALSO bound the upstream call count
# via the idempotency key so we don't double-charge for an LLM turn.
#
# WHY THE 5 HEADERS (not just the 3 the directive lists)?
# The Day-4C directive lists `X-User-Id`, `X-Idempotency-Key`,
# `X-Request-Id`. The current internal-rpc contract on main lists
# `X-Internal-Caller`, `X-Trace-Id`, `X-User-Id`. We send ALL FIVE so
# both the directive AND the contract are satisfied; coordinator can
# canonicalize later (per the I6 push-back surfaced in the Day-4C PR
# body). X-Trace-Id carries the same value as X-Request-Id so the
# Langfuse correlation works regardless of which header the
# orchestrator reads.
#
# WHY ai_influencer_id IS A PLACEHOLDER STRING?
# Per interface-contracts/01-internal-rpc-contracts.md the orchestrator
# expects `ai_influencer_id` in the request body. Public-api currently
# has no persistent conversation → influencer mapping (that lands when
# Session 5's Day-9 ETL ports data + Day-5+ wires the conversation
# table). For Day-4C we send a fixed placeholder string so the wire
# call works; orchestrator-side validation may accept it (stub mode)
# OR reject as 422 (real-data mode) — both are valid mappings handled
# by the chat handler's error path.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Optional

import httpx

from app.config import get_settings


# Module-level reference set by lifespan startup. None until startup
# completes; raises a RuntimeError on access before startup (loud
# failure beats silent None.post() crash later).
_async_client: Optional[httpx.AsyncClient] = None


# Caller name embedded in X-Internal-Caller per the internal-rpc
# contract. Hardcoded here (not in shared-config) because it identifies
# THIS service uniquely.
INTERNAL_CALLER_NAME = "yral-rishi-agent-public-api"


def init_orchestrator_client() -> None:
    """Construct the singleton httpx.AsyncClient for orchestrator calls.

    WHAT: builds an httpx.AsyncClient pointed at settings.orchestrator_base_url
          with the connect + total timeouts settings.orchestrator_*. Stores
          on the module-level _async_client reference.
    WHEN: called from app/main.py's lifespan startup phase.
    WHY:  lifespan ownership means ONE pool per worker; closed gracefully
          via close_orchestrator_client() on SIGTERM.
    """
    global _async_client  # noqa: PLW0603 — module-level singleton is intentional
    settings = get_settings()
    # `timeout` accepts a httpx.Timeout for fine-grained control over
    # connect vs read vs write vs pool. Day-4C's directive specifies
    # connect=5s, total=30s; we map connect→connect, read+write+pool→30s
    # so the same overall budget applies to any phase that's slow.
    timeout = httpx.Timeout(
        timeout=settings.orchestrator_request_timeout_seconds,
        connect=settings.orchestrator_connect_timeout_seconds,
    )
    _async_client = httpx.AsyncClient(
        base_url=settings.orchestrator_base_url,
        timeout=timeout,
    )


async def close_orchestrator_client() -> None:
    """Close + null the singleton client.

    WHAT: awaits _async_client.aclose() if present + nulls the reference.
    WHEN: lifespan shutdown phase (SIGTERM, Swarm rolling update, etc.).
    WHY:  drains pending connections cleanly; without this, hung
          orchestrator calls could keep the worker alive past the
          shutdown grace period.
    """
    global _async_client  # noqa: PLW0603
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


def get_orchestrator_client() -> httpx.AsyncClient:
    """Return the lifespan-managed singleton client.

    WHAT: returns _async_client; raises RuntimeError if lifespan startup
          hasn't run.
    WHEN: called from `run_turn` (and any future per-handler caller).
    WHY:  loud failure on misuse beats a silent NoneType.post() crash.
    """
    if _async_client is None:
        raise RuntimeError(
            "orchestrator client not initialized — was init_orchestrator_client() "
            "called from app lifespan startup?"
        )
    return _async_client


async def run_turn(
    *,
    user_id: str,
    conversation_id: str,
    message_content: str,
    client_message_id: Optional[str],
    media_urls: Optional[list[str]],
    request_id: str,
    idempotency_key: str,
) -> httpx.Response:
    """POST one chat turn to the Session-4 orchestrator.

    WHAT: builds the request body per the internal-rpc contract, attaches
          the 5 internal-call headers, awaits httpx.AsyncClient.post()
          against the configured path, returns the raw httpx.Response so
          the chat handler can interpret status + JSON body.
    WHEN: invoked from chat_routes.send_message() on every cache-miss
          message-send turn.
    WHY:  single place that knows the orchestrator's wire shape; the
          chat handler stays thin (auth, idempotency, error mapping)
          and the wire details live here.
    """
    settings = get_settings()
    client = get_orchestrator_client()

    body = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        # Per the file header — orchestrator-side ai_influencer_id
        # lookup lands when Session 5's Day-9 ETL ports conversation
        # data + Day-5+ wires the conversation table. Day-4C sends a
        # fixed placeholder so the wire call works.
        "ai_influencer_id": "placeholder-day-4c-pending-conversation-lookup",
        "message_content": message_content,
        # Empty string when mobile didn't send client_message_id; the
        # contract field is required-string, so null isn't valid. Empty
        # string matches chat-ai's current behavior for orphan messages.
        "client_message_id": client_message_id or "",
        "media_urls": media_urls,
    }

    headers = {
        # The X-User-Id forward Day-4C directive specifies.
        "X-User-Id": user_id,
        # F10 dedup key — orchestrator can use this for its own dedup
        # layer too if it wants. (Public-api dedupes BEFORE this call;
        # orchestrator sees the key only on cache-miss requests.)
        "X-Idempotency-Key": idempotency_key,
        # Day-4C directive's correlation header (read by Langfuse,
        # logs, Sentry breadcrumbs on the orchestrator side).
        "X-Request-Id": request_id,
        # Per the internal-rpc contract on main — identifies which
        # service originated this call. Hardcoded since it's THIS
        # service's identity.
        "X-Internal-Caller": INTERNAL_CALLER_NAME,
        # Per the internal-rpc contract on main — same value as
        # X-Request-Id so Langfuse correlates regardless of which
        # header the orchestrator reads. Two headers carry the same
        # value pending coordinator canonicalization (I6 push-back
        # surfaced in the Day-4C PR body).
        "X-Trace-Id": request_id,
    }

    return await client.post(
        settings.orchestrator_run_turn_path,
        json=body,
        headers=headers,
    )


# ===========================================================================
# RELATED FILES:
#   main.py                  — lifespan startup calls init_orchestrator_client;
#                              shutdown calls close_orchestrator_client
#   api/chat_routes.py       — send_message handler invokes run_turn()
#   api/idempotency.py       — supplies the idempotency_key (cache key
#                              derivation; the orchestrator just sees the value)
#   config.py                — orchestrator_base_url / _run_turn_path /
#                              _request_timeout_seconds / _connect_timeout_seconds
#   request_id_middleware.py — supplies the X-Request-Id value
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — the contract this client honors
# ===========================================================================
