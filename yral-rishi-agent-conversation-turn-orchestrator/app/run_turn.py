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
# It is the INTERNAL RPC that Session 3's public-api calls when a
# mobile user sends a chat message. Public-api:
#   1. Validates the user's JWT, looks up the conversation row,
#   2. Persists the user message,
#   3. Calls `POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn`,
#   4. Receives a `MessageResponse` reply,
#   5. Wraps in `ApiResponse<MessageResponse>{success=true, msg='OK', error=null, data=...}`,
#   6. Returns to the mobile client.
# The orchestrator handles step 4; everything else stays with public-api.
#
# WHY F10 IDEMPOTENCY IS REQUIRED + ATOMIC + FINGERPRINT-CHECKED
# Per CONSTRAINTS F10 verbatim ("default-on on all non-GET endpoints;
# dedupes via Redis 24hr TTL") + the coordinator's PR #98 commit
# 31d1dac contract update + Codex PR-#96 round-3 review:
#   - `X-Idempotency-Key` REQUIRED on every call (Codex round-3
#     BLOCKER 1a). Missing → 400 with ApiResponse envelope, NOT a
#     bare 503 / NOT a server-generated UUID4.
#   - Dedup atomic against concurrent duplicates (Codex round-3
#     BLOCKER 1b). The `app.idempotency.acquire_or_check` helper
#     uses a single `SET NX` as its critical section; concurrent
#     duplicate requests poll the key until the holder marks
#     completion.
#   - Fingerprint check (Codex round-3 BLOCKER 1b). Same key + same
#     body → byte-identical replay. Same key + DIFFERENT body → 409
#     with envelope (catches client bugs where the same key was
#     reused across two different requests).
#
# WHY PLAIN JSON, NOT SSE?
# A16 (feature parity) + the Session-4 agent definition's Day-2 plan +
# Rishi's typed `continue` 2026-05-18 with the explicit "plain JSON,
# NOT SSE — A16 parity" directive. The mobile client today expects a
# single-shot `MessageResponse` from
# `POST /api/v1/chat/conversations/{id}/messages`. v2 must match. SSE
# streaming, when it lands, gets its own `/v2/turn-stream` path
# behind a feature flag, never on the v1 path.
#
# Coordinator PR #98 (commit 31d1dac) updated the cross-service
# contract at `interface-contracts/01-internal-rpc-contracts.md` to
# spell out the F10 + C11 + 400-reject requirements verbatim. This
# handler implements those.
#
# WHY THE 4xx/5xx ERROR PATHS USE THE ApiResponse ENVELOPE
# The contract update (31d1dac) explicitly specifies envelope-shaped
# error responses for the three idempotency-specific cases
# (400/409/503-in-flight). The successful 200 path returns the bare
# `MessageResponse` per the existing internal-RPC convention (the
# envelope wrap happens at the public-api boundary, not here). The
# environment / feature-flag 503s keep their HTTPException `detail`
# shape — those are different error categories not covered by the
# contract update.
#
# WHY TWO GATES (environment + feature flag) FIRE BEFORE IDEMPOTENCY?
# Defence-in-depth. A 503-emitting environment never reads or writes
# Redis — the gate check short-circuits BEFORE the F10 layer
# engages. The gate-respect tests prove jailbreak inputs in
# production still 503 (no leak via idempotency-layer bypass).
#
# WHY ONE ROUTE HANDLER, NOT A FastAPI DEPENDENCY CHAIN?
# Per A2.1 — keep the Day-2 stub flat + readable. Dependencies for
# the real handler (Soul-File lookup, LLM client, safety stack) land
# in their own Day-3/4/5 PRs; introducing a dependency framework
# before the consumers exist is premature abstraction.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `datetime` + `timezone` build the ISO8601 UTC `created_at` timestamp
# the chat-ai parity contract requires on every assistant reply.
from datetime import datetime, timezone

# stdlib logger — emits structured fields the H6 PII-allowlist redactor
# in `app/logging.py` knows about. We log idempotency hit/miss / key
# provenance / fingerprint-mismatch reasons; NEVER the user_message
# content or the cached payload itself.
import logging

# `Annotated` lets a single parameter's type carry BOTH the underlying
# Python type AND the FastAPI `Header(...)` binding without falling
# back to default-value syntax (which Pydantic v2 deprecates for body
# parameters and FastAPI plans to deprecate for headers).
from typing import Annotated

# `uuid` — module-level access to `uuid.UUID(...)` for the round-4
# X-Idempotency-Key validation (BLOCKER 3). `uuid4` generates the
# per-reply `id` field on the MessageResponse. Round-3 fixup removed
# the server-generated fallback X-Idempotency-Key path entirely
# (BLOCKER 1a); the header is required from the caller now AND must
# parse as a UUID per BLOCKER 3.
import uuid
from uuid import uuid4

# stdlib SHA-256 — round-4 BLOCKER 3: invalid X-Idempotency-Key values
# are logged as their first-16-chars-of-sha256 hash, NEVER as the
# raw value (H6 defence-in-depth even on the reject path; the
# caller could have stuffed PII into the header).
import hashlib

# FastAPI's `APIRouter` lets us hang this module's routes off the
# service's `app` object without polluting `main.py` with imports.
# `Header` binds HTTP request headers to typed parameters.
# `HTTPException` is the documented way to short-circuit a route
# with a specific status (used for the gate-closed 503s — those keep
# the FastAPI `detail` shape).  `status` is FastAPI's namespaced
# HTTP-status-code constants — using them (vs raw 400 / 503) keeps
# callsites self-documenting.
from fastapi import APIRouter, Header, HTTPException, status

# `JSONResponse` lets the handler return arbitrary JSON-shaped bodies
# WITHOUT triggering the route's `response_model=MessageResponse`
# validation. Used for the three idempotency-specific error
# envelopes (400 / 409 / 503-in-flight) per the contract update at
# PR #98 commit 31d1dac.
from fastapi.responses import JSONResponse

# `get_settings()` reads the typed Settings singleton — `environment`
# (production gate), `enable_run_turn_stub` (explicit opt-in). The
# round-3 fixup also adds `redis_sentinel_enabled` / `redis_url` —
# consumed by `app/idempotency.py`, not directly here.
from app.config import get_settings

# F10 idempotency module — round-3 swap from the prior
# `get_cached_response` / `cache_response` pair to the atomic-lock
# API. `acquire_or_check` returns a typed decision; `mark_complete`
# overwrites the in-progress lock with the completed response.
from app.idempotency import (
    acquire_or_check,
    compute_idempotency_key,
    compute_request_fingerprint,
    mark_complete,
)

# Pydantic models the route declares — FastAPI uses these for
# request parsing + response serialisation. `MessageResponse` is
# BYTE-IDENTICAL to chat-ai's MessageResponse per A8 + A16.
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
# ApiResponse envelope builders (idempotency-specific error paths)
# ===========================================================================


def _api_response_envelope(error_code: str, message_text: str) -> dict:
    """Build the ApiResponse-shaped error envelope.

    WHAT: returns `{"success": False, "msg": message_text,
          "error": error_code, "data": None}` per the contract at
          `interface-contracts/00-api-contract.md`.
    WHEN: called by the three idempotency-specific error paths in
          the route handler (400 / 409 / 503-in-flight).
    WHY:  contract update at PR #98 commit 31d1dac requires
          envelope-shaped error responses for the idempotency error
          cases. Centralising the shape in one helper means a future
          contract bump (e.g. adding a fifth field) edits one
          location.
    """
    return {
        "success": False,
        "msg": message_text,
        "error": error_code,
        "data": None,
    }


# ===========================================================================
# Handlers
# ===========================================================================


@router.post(
    "/v1/turn",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Run one conversation turn (Day-2 stub, F10-dedup, C11-Redis).",
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
):
    """Day-2 stub for the internal run_turn RPC, behind two gates + atomic F10.

    WHAT: gates → idempotency-key header required check → atomic-acquire
          the in-progress lock → on `replay_done` return cached payload,
          on `fingerprint_mismatch` return 409 envelope, on
          `in_flight_timeout` return 503 envelope, on `acquired` build
          the schema-valid placeholder + mark_complete + return.
    WHEN: invoked by Session 3's public-api on every chat-message turn.
    WHY:  unblocks Session 3's integration work + locks in atomic F10
          dedup (Codex PR-#96 round-3 BLOCKER 1). The two gates keep
          the stub out of production parity-test traffic; the 24h
          Redis dedup serves a byte-identical replay for the F10
          happy path.

    The `request_id` header is PASSED THROUGH (read but not used in
    the response path); Day-3+ wires it into Langfuse trace metadata.
    """
    # -----------------------------------------------------------------------
    # GATES (must run BEFORE Redis touches — a production env should
    # never ALSO be reading/writing the idempotency cache).
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
    # X-User-Id REQUIRED (Codex round-4 BLOCKER 2).
    # -----------------------------------------------------------------------
    # The previous round-3 code fell back to an "unknown-user" sentinel
    # when X-User-Id was absent. That collapsed the idempotency cache
    # scope — two unrelated callers with missing headers could replay
    # each other's cached responses (a cross-tenant data-leak shape).
    # Public-api forwards X-User-Id after JWT validation per E6; a
    # missing header here means the caller bypassed public-api OR
    # public-api has a wiring bug. Either way: reject with 400 envelope.
    if user_id is None:
        _log.warning(
            "user_id_header_required_but_missing",
            extra={"conversation_id": request.conversation_id},
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_api_response_envelope(
                error_code="user_id_header_required",
                message_text=(
                    "X-User-Id header is required for POST /v1/turn "
                    "(per E6 — public-api forwards the JWT-validated user_id "
                    "to every internal RPC; missing here = caller bypassed "
                    "public-api OR public-api has a wiring bug)."
                ),
            ),
        )

    # -----------------------------------------------------------------------
    # X-Idempotency-Key REQUIRED + must be a UUID
    # (Codex round-3 BLOCKER 1a + round-4 BLOCKER 3).
    # -----------------------------------------------------------------------
    # Missing header → 400 with ApiResponse-shaped envelope (NOT a bare
    # HTTPException detail). F10 + the contract at PR #98 31d1dac say
    # the header is required on every call; the server-side-generated
    # UUID4 fallback from the round-2 fix is GONE.
    if idempotency_key is None:
        _log.warning(
            "idempotency_key_required_but_missing",
            extra={
                "conversation_id": request.conversation_id,
                "user_id_present": True,  # user_id check already passed
            },
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_api_response_envelope(
                error_code="idempotency_key_required",
                message_text=(
                    "X-Idempotency-Key header is required for POST /v1/turn "
                    "(F10 default-on idempotency; contract at "
                    "interface-contracts/01-internal-rpc-contracts.md)."
                ),
            ),
        )

    # Validate as UUID (Codex round-4 BLOCKER 3). Without this gate a
    # malicious or buggy client can pass arbitrary text (including PII
    # or message-content) in the header — that text would then land in
    # Redis keys + structured logs (H6 violation surface). The UUID
    # constraint bounds the value to a known-non-PII shape by
    # construction. `uuid.UUID(...)` raises ValueError on invalid
    # input; we catch + emit a 400 envelope instead of a 5xx.
    try:
        uuid.UUID(idempotency_key)
    except ValueError:
        # Log the HASH of the offending value, never the value itself
        # (Codex round-4 BLOCKER 3 — H6 defence-in-depth even on the
        # reject path).
        offending_value_hash_prefix = hashlib.sha256(
            idempotency_key.encode("utf-8"),
        ).hexdigest()[:16]
        _log.warning(
            "idempotency_key_invalid_format",
            extra={
                "conversation_id": request.conversation_id,
                "idempotency_key_hash_prefix": offending_value_hash_prefix,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_api_response_envelope(
                error_code="idempotency_key_invalid_format",
                message_text=(
                    "X-Idempotency-Key must be a UUID (RFC 4122). "
                    "Mobile clients should generate it via the standard "
                    "platform UUID API; arbitrary text is rejected so the "
                    "header value cannot leak PII into Redis keys or logs."
                ),
            ),
        )

    # -----------------------------------------------------------------------
    # KEY + FINGERPRINT.
    # -----------------------------------------------------------------------
    redis_key = compute_idempotency_key(
        user_id=user_id,
        idempotency_key=idempotency_key,
    )

    # Fingerprint of the request body (canonical-JSON SHA-256). Same
    # body → same fingerprint → byte-identical replay. Different body
    # with same idempotency key → fingerprint mismatch → 409 envelope.
    request_fingerprint = compute_request_fingerprint(
        request.model_dump(mode="json"),
    )

    # -----------------------------------------------------------------------
    # ATOMIC DEDUP (Codex round-3 BLOCKER 1b).
    # -----------------------------------------------------------------------
    # One `SET NX` is the critical section. The decision dataclass
    # tells the handler which of the four flows to take.
    decision = await acquire_or_check(
        redis_key=redis_key,
        fingerprint=request_fingerprint,
    )

    if decision.state == "replay_done":
        # Byte-identical replay — return the cached payload as
        # MessageResponse. Pydantic re-validates the cached shape
        # (cheap; catches a corrupted cache entry that slipped past
        # the JSON-decode try/except in idempotency.py).
        return MessageResponse(**decision.cached_response)

    if decision.state == "fingerprint_mismatch":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_api_response_envelope(
                error_code="idempotency_key_reused_with_different_body",
                message_text=(
                    "X-Idempotency-Key was reused with a different request "
                    "body; mobile clients must generate a fresh key per "
                    "distinct request payload."
                ),
            ),
        )

    if decision.state == "in_flight_timeout":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_api_response_envelope(
                error_code="idempotency_in_flight",
                message_text=(
                    "Another request with this X-Idempotency-Key is still "
                    "in flight; retry after a short backoff."
                ),
            ),
        )

    # decision.state == "acquired" — we won the SET NX race; proceed.

    # -----------------------------------------------------------------------
    # STUB PROCESSING (Day-5+ replaces this with the real LLM call;
    # safety stack from Day-3 runs as middleware OUTSIDE this handler).
    # -----------------------------------------------------------------------

    # ISO8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`. Matches chat-ai's wire
    # shape; renamed from the round-2 `now_iso` per Codex round-3
    # BLOCKER 3 (B2 disallows the `iso` abbreviation).
    current_utc_timestamp_text = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )

    response = MessageResponse(
        id=str(uuid4()),
        conversation_id=request.conversation_id,
        role="assistant",
        content=STUB_CONTENT,
        media_urls=None,
        client_message_id=None,
        created_at=current_utc_timestamp_text,
        count_toward_paywall=True,
    )

    # Overwrite the in-progress lock with the completed response.
    # Every concurrent waiter that's polling will harvest this payload
    # on its next GET. `model_dump(mode="json")` serialises Pydantic-
    # typed values to JSON-native primitives so the `json.dumps`
    # inside `mark_complete` always succeeds.
    await mark_complete(
        redis_key=redis_key,
        fingerprint=request_fingerprint,
        response_payload=response.model_dump(mode="json"),
    )

    return response


# ===========================================================================
# RELATED FILES:
#   main.py            — imports + mounts `router`; lifespan init/close Redis
#   config.py          — `enable_run_turn_stub` + `environment` +
#                        `redis_url` + `redis_sentinel_enabled` settings
#   idempotency.py     — F10 atomic-dedup helpers (acquire_or_check +
#                        mark_complete + compute_request_fingerprint)
#   models/turn.py     — RunTurnRequest + MessageResponse Pydantic models
#   ../tests/test_run_turn.py
#                      — happy + error + idempotency-replay + concurrent
#                        + fingerprint-mismatch coverage
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                      — chat-ai MessageResponse parity contract + the
#                        ApiResponse envelope shape used in error paths
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                      — internal RPC surface; PR #98 commit 31d1dac
#                        spells out C11 + atomic dedup + 400-reject
#                        which this handler implements verbatim
#   ../../.claude/agents/session-4-orchestrator.md
#                      — Day-2 plan + JSON-not-SSE directive this file
#                        implements
# ===========================================================================
