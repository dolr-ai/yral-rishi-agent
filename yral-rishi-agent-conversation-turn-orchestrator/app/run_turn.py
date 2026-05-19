# ---------------------------------------------------------------------------
# run_turn.py — Day-2 `POST /v1/turn` RPC endpoint (Session 3 → orchestrator).
#
# ⭐ START HERE: this module exposes ONE thing — `router`, a FastAPI
# APIRouter that `app/main.py` mounts on the FastAPI app. The router
# defines one route, `POST /v1/turn`, which TODAY (Day 2) returns a
# schema-valid stub `MessageResponse` with a placeholder body. Day 5
# swaps the stub for the real LLM call; Day 3 adds the safety stack
# (H5 prompt-injection → H4 crisis → A10 NSFW) IN FRONT of this handler
# without touching the route signature.
#
# WHAT IS THIS ENDPOINT FOR?
# It is the INTERNAL RPC that Session 3's public-api calls when a mobile
# user sends a chat message. Public-api:
#   1. Validates the user's JWT, looks up the conversation row,
#   2. Persists the user message,
#   3. Calls `POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn`,
#   4. Receives a `MessageResponse` reply,
#   5. Wraps in `ApiResponse<MessageResponse>{success=true, msg='OK', error=null, data=...}`,
#   6. Returns to the mobile client.
# The orchestrator handles step 4; everything else stays with public-api.
#
# WHY F10 IDEMPOTENCY IS THE FIRST THING THIS HANDLER DOES
# Per CONSTRAINTS F10 verbatim ("default-on on all non-GET endpoints;
# dedupes via Redis 24hr TTL") + Codex PR-#96 BLOCKER 1: the handler
# computes the user-scoped idempotency key, looks up Redis BEFORE any
# other work, and on HIT replays the cached MessageResponse byte-for-
# byte. On MISS the handler processes normally + writes the response
# through to Redis before returning. A missing X-Idempotency-Key
# header gets a server-generated UUID4 + a structured log marker
# (client_provided_key=false) so Langfuse traces (Day-5+) can
# distinguish client-generated vs server-generated keys.
#
# WHY PLAIN JSON, NOT SSE?
# A16 (feature parity) + the Session-4 agent definition's Day-2 plan +
# Rishi's typed `continue` 2026-05-18 with the explicit "plain JSON,
# NOT SSE — A16 parity" directive. The mobile client today expects a
# single-shot `MessageResponse` from
# `POST /api/v1/chat/conversations/{id}/messages`. v2 must match. SSE
# streaming, when it lands, gets its own `/v2/turn-stream` path behind
# a feature flag, never on the v1 path.
#
# Coordinator PR #98 (commit f708a49) updated the cross-session contract
# at `interface-contracts/01-internal-rpc-contracts.md` to match this
# handler's actual shape (JSON `MessageResponse`, media_urls +
# client_message_id in the request, idempotency required day-1).
#
# WHY TWO GATES (environment + feature flag)?
# Defence-in-depth. The Day-2 stub MUST NOT reach mobile parity-test
# traffic. The environment gate (`!= "production"`) is the
# unconditional safety net; the feature flag (`enable_run_turn_stub`)
# is the explicit opt-in. Either gate failing returns 503 with a
# generic message. F10 idempotency is checked AFTER both gates so a
# 503-emitting environment never reads or writes Redis.
#
# WHY ONE ROUTE HANDLER, NOT A FastAPI DEPENDENCY CHAIN?
# Per A2.1 — keep the Day-2 stub flat + readable. Dependencies for the
# real handler (Soul-File lookup, LLM client, safety stack) land in
# their own Day-3/4/5 PRs; introducing a dependency framework before
# the consumers exist is premature abstraction.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `datetime` + `timezone` build the ISO8601 UTC `created_at` timestamp
# the chat-ai parity contract requires on every assistant reply.
from datetime import datetime, timezone

# stdlib logger — emits structured fields the H6 PII-allowlist redactor
# in `app/logging.py` knows about. We log idempotency hit/miss + key
# provenance (client_provided vs server_generated), never the
# user_message content or the cached payload itself.
import logging

# `Annotated` lets a single parameter's type carry BOTH the underlying
# Python type AND the FastAPI `Header(...)` binding without falling
# back to default-value syntax (which Pydantic v2 deprecates for body
# parameters and FastAPI plans to deprecate for headers).
from typing import Annotated

# `uuid4` generates the per-reply `id` field on the MessageResponse +
# the server-side fallback X-Idempotency-Key when the caller didn't
# send one (every reply still needs a stable dedup key for Redis).
from uuid import uuid4

# FastAPI's `APIRouter` lets us hang this module's routes off the
# service's `app` object without polluting `main.py` with imports.
# `Header` binds HTTP request headers to typed parameters. `HTTPException`
# is the documented way to short-circuit a route with a specific status.
# `status` is FastAPI's namespaced HTTP-status-code constants — using
# them (vs raw 503 / 404) keeps callsites self-documenting.
from fastapi import APIRouter, Header, HTTPException, status

# `get_settings()` reads the typed Settings singleton — `environment`
# (production gate), `enable_run_turn_stub` (explicit opt-in),
# `redis_url` (idempotency layer points here).
from app.config import get_settings

# F10 idempotency module — provides the Redis-backed dedup layer.
# `compute_idempotency_key` formats the scoped Redis key;
# `get_cached_response` / `cache_response` are the read/write
# helpers. Lifespan (in `main.py`) opens / closes the underlying
# Redis client.
from app.idempotency import (
    cache_response,
    compute_idempotency_key,
    get_cached_response,
)

# Pydantic models the route declares — FastAPI uses these for request
# parsing + response serialisation. `MessageResponse` is BYTE-IDENTICAL
# to chat-ai's MessageResponse per A8 + A16.
from app.models.turn import MessageResponse, RunTurnRequest


# Day-2 stub content. The literal placeholder string the agent
# definition specifies, with the "from day-5" timing per Rishi's
# green-light 2026-05-18. Future readers spotting this in logs will
# know exactly which PR shipped it — searchable by the bracketed
# prefix.
STUB_CONTENT: str = (
    "[v2 phase-1 day-2 orchestrator stub — real LLM response from day-5]"
)


_log = logging.getLogger("app.run_turn")


# ===========================================================================
# Router
# ===========================================================================

# FastAPI APIRouter — main.py imports + mounts this on the app. The
# `tags` argument groups this route under "run_turn" in the OpenAPI
# /docs UI so a non-programmer browsing the docs sees the run_turn
# block clearly. No `prefix` here; the `/v1/...` lives in the route
# decorator so callers searching the codebase for `/v1/turn` find
# this file directly.
router = APIRouter(tags=["run_turn"])


# ===========================================================================
# Handlers
# ===========================================================================


@router.post(
    "/v1/turn",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Run one conversation turn (Day-2 stub).",
)
async def run_turn(
    request: RunTurnRequest,
    user_id: Annotated[
        str | None, Header(alias="X-User-Id")
    ] = None,
    idempotency_key: Annotated[
        str | None, Header(alias="X-Idempotency-Key")
    ] = None,
    request_id: Annotated[
        str | None, Header(alias="X-Request-Id")
    ] = None,
) -> MessageResponse:
    """Day-2 stub for the internal run_turn RPC, behind two gates + F10 dedup.

    WHAT: gates check → idempotency lookup → on HIT replay cached
          MessageResponse byte-for-byte → on MISS build the schema-
          valid placeholder, cache it, return.
    WHEN: invoked by Session 3's public-api on every chat-message turn.
    WHY:  unblocks Session 3's integration work + locks in F10 dedup
          from day 1 (Codex PR-#96 BLOCKER 1). The two gates keep the
          stub out of production parity-test traffic; the 24h Redis
          dedup keeps the same idempotency key from triggering two
          server-side stub renderings.

    The `request_id` header is PASSED THROUGH (read but not used in the
    response path); Day-3+ wires it into Langfuse trace metadata.
    """
    # -----------------------------------------------------------------------
    # GATES (must run BEFORE Redis touches — a production env should never
    # ALSO be reading/writing the idempotency cache).
    # -----------------------------------------------------------------------
    settings = get_settings()

    # Gate 1: unconditional production safety net.
    if settings.environment == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run_turn stub disabled — real LLM enablement is Day-5.",
        )

    # Gate 2: explicit opt-in feature flag (off by default everywhere).
    if not settings.enable_run_turn_stub:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run_turn stub disabled — set ENABLE_RUN_TURN_STUB=true to enable.",
        )

    # -----------------------------------------------------------------------
    # F10 IDEMPOTENCY (Codex PR-#96 BLOCKER 1).
    # -----------------------------------------------------------------------

    # Generate a server-side X-Idempotency-Key when the caller didn't
    # send one. F10 says idempotency is DEFAULT-ON, not "client-provided
    # only" — every non-GET endpoint must dedupe. The provenance flag
    # is logged so Day-5+ Langfuse traces can distinguish
    # client-generated vs server-generated keys (mostly useful for
    # spotting clients that forgot to send the header — a Day-5
    # observability win).
    client_provided_key = idempotency_key is not None
    effective_idempotency_key = idempotency_key or str(uuid4())

    # User scope — without a user_id we can't safely cache responses
    # (per the Day-2-fixup directive: "scope by user_id from X-User-Id
    # header so different users with the same client-generated key
    # never collide"). Public-api forwards X-User-Id after JWT
    # validation per E6; a missing header in production is a contract
    # violation by the caller. For Day-2 we fall back to a
    # `unknown-user` sentinel so the test path still works without an
    # auth layer; Day-5+ tightens this to 400-on-missing once Session
    # 3 always forwards the header.
    effective_user_id = user_id or "unknown-user"

    redis_key = compute_idempotency_key(
        user_id=effective_user_id,
        idempotency_key=effective_idempotency_key,
    )

    # Cache lookup — on HIT replay the cached MessageResponse without
    # touching the (today-stub, Day-5+ real LLM) processing path. The
    # cached payload is the SAME dict FastAPI would have serialised
    # itself, so the replay response is byte-equal to a fresh one.
    cached = await get_cached_response(redis_key)
    if cached is not None:
        _log.info(
            "idempotency_hit",
            extra={
                "client_provided_key": client_provided_key,
                "conversation_id": request.conversation_id,
                "user_id": effective_user_id,
            },
        )
        # `MessageResponse(**cached)` round-trips through Pydantic to
        # re-validate the cached shape (cheap; catches a corrupted
        # cache entry that slipped through `get_cached_response`'s
        # json-decode try/except).
        return MessageResponse(**cached)

    # Cache MISS → process normally.
    _log.info(
        "idempotency_miss",
        extra={
            "client_provided_key": client_provided_key,
            "conversation_id": request.conversation_id,
            "user_id": effective_user_id,
        },
    )

    # -----------------------------------------------------------------------
    # STUB PROCESSING (Day-5+ replaces this with the real LLM call;
    # safety stack from Day-3 runs as middleware OUTSIDE this handler).
    # -----------------------------------------------------------------------

    # Build the schema-valid placeholder. The values match what chat-ai
    # writes today for an assistant reply: fresh UUID, the conversation
    # id from the request, role=assistant, the literal placeholder
    # content, no media, no client_message_id, ISO8601 UTC timestamp,
    # and count_toward_paywall=True (Day-3 safety stack will flip this
    # to False for safety-blocked replies).
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    response = MessageResponse(
        id=str(uuid4()),
        conversation_id=request.conversation_id,
        role="assistant",
        content=STUB_CONTENT,
        media_urls=None,
        client_message_id=None,
        created_at=now_iso,
        count_toward_paywall=True,
    )

    # Cache the response under the idempotency key with 24h TTL. The
    # next call with the same key replays this exact payload.
    # `model_dump(mode="json")` serialises Pydantic-typed values
    # (datetimes, Literals, etc.) to JSON-native primitives so the
    # `json.dumps` inside `cache_response` always succeeds.
    await cache_response(redis_key, response.model_dump(mode="json"))

    return response


# ===========================================================================
# RELATED FILES:
#   main.py            — imports + mounts `router`; lifespan init/close Redis
#   config.py          — `enable_run_turn_stub` setting + `environment` tag
#                        + `redis_url` (where idempotency reads/writes)
#   idempotency.py     — F10 Redis dedup helpers consumed above
#   models/turn.py     — RunTurnRequest + MessageResponse Pydantic models
#   ../tests/test_run_turn.py
#                      — happy + error + idempotency-replay coverage
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                      — chat-ai MessageResponse parity contract
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                      — internal RPC surface; PR #98 (commit f708a49)
#                        aligned the contract with this handler's actual
#                        shape (JSON, media_urls, client_message_id,
#                        idempotency-required-day-1)
#   ../../.claude/agents/session-4-orchestrator.md
#                      — Day-2 plan + JSON-not-SSE directive this file
#                        implements
# ===========================================================================
