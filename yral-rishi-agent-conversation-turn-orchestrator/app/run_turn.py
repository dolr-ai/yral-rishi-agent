# ---------------------------------------------------------------------------
# run_turn.py — Day-2 `POST /v1/turn` RPC endpoint (Session 3 → orchestrator).
#
# ⭐ START HERE: this module exposes ONE thing — `router`, a FastAPI
# APIRouter that `app/main.py` mounts on the FastAPI app. The router
# defines one route, `POST /v1/turn`, which TODAY (Day 2) returns a
# schema-valid stub `MessageDto` with a placeholder body. Day 5 swaps
# the stub for the real LLM call; Day 3 adds the safety stack
# (H5 prompt-injection → H4 crisis → A10 NSFW) IN FRONT of this handler
# without touching the route signature.
#
# WHAT IS THIS ENDPOINT FOR?
# It is the INTERNAL RPC that Session 3's public-api calls when a mobile
# user sends a chat message. Public-api:
#   1. Validates the user's JWT, looks up the conversation row,
#   2. Persists the user message,
#   3. Calls `POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn`,
#   4. Receives a MessageDto reply,
#   5. Wraps in `ApiResponse<MessageDto>{success=true, msg='OK', error=null, data=...}`,
#   6. Returns to the mobile client.
# The orchestrator handles step 4; everything else stays with public-api.
#
# WHY PLAIN JSON, NOT SSE?
# A16 (feature parity) + the Session-4 agent definition's Day-2 plan +
# Rishi's typed `continue` 2026-05-18 with the explicit "plain JSON,
# NOT SSE — A16 parity" directive. The mobile client today expects a
# single-shot `MessageDto` from `POST /api/v1/chat/conversations/{id}/messages`.
# v2 must match. SSE streaming, when it lands, gets its own
# `/v2/turn-stream` path behind a feature flag, never on the v1 path.
#
# NOTE: `interface-contracts/01-internal-rpc-contracts.md` (coordinator-
# owned) still shows the older "POST /turn + SSE response" shape from
# pre-A16 planning. DEP raised in cross-session-dependencies.md asking
# coordinator to update it. Until that lands, the agent def + this file
# are the authoritative source for Session 3 to integrate against.
#
# WHY TWO GATES (environment + feature flag)?
# Defence-in-depth. The Day-2 stub MUST NOT reach mobile parity-test
# traffic. The environment gate (`!= "production"`) is the unconditional
# safety net; the feature flag (`enable_run_turn_stub`) is the explicit
# opt-in. Either gate failing returns 503 with a generic message.
#
# WHY ONE ROUTE HANDLER, NOT A FastAPI DEPENDENCY CHAIN?
# Per A2.1 — keep the Day-2 stub flat + readable. Dependencies for the
# real handler (Soul-File lookup, LLM client, safety stack) land in
# their own Day-3/4/5 PRs; introducing a dependency framework before the
# consumers exist is premature abstraction.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.models.turn import MessageDto, RunTurnRequest


# Day-2 stub content. The literal placeholder string the agent definition
# specifies, with the "from day-5" timing per Rishi's green-light
# 2026-05-18. Future readers spotting this in logs will know exactly
# which PR shipped it — searchable by the bracketed prefix.
STUB_CONTENT: str = (
    "[v2 phase-1 day-2 orchestrator stub — real LLM response from day-5]"
)


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
    response_model=MessageDto,
    status_code=status.HTTP_200_OK,
    summary="Run one conversation turn (Day-2 stub).",
)
async def run_turn(
    request: RunTurnRequest,
    idempotency_key: Annotated[
        str | None, Header(alias="X-Idempotency-Key")
    ] = None,
    request_id: Annotated[
        str | None, Header(alias="X-Request-Id")
    ] = None,
) -> MessageDto:
    """Day-2 stub for the internal run_turn RPC.

    WHAT: returns a schema-valid placeholder MessageDto when the two
          gate conditions are satisfied. Otherwise returns 503.
    WHEN: invoked by Session 3's public-api on every chat-message turn.
    WHY:  unblocks Session 3's integration work without waiting on
          Days 3-5 safety stack + LLM enablement. Behind two safety
          gates so the stub cannot leak into production parity tests.

    The `idempotency_key` and `request_id` headers are PASSED THROUGH
    today (accepted + ignored beyond shape validation). Day 3 wires
    them into the safety-stack middleware + Langfuse trace metadata;
    Day 5 uses idempotency_key to short-circuit duplicate LLM calls
    via Redis dedup per F10.
    """
    # Two-gate refusal — see file-header rationale for why both exist.
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

    # Build the schema-valid placeholder. The values match what chat-ai
    # writes today for an assistant reply: fresh UUID, the conversation
    # id from the request, role=assistant, the literal placeholder
    # content, no media, no client_message_id, ISO8601 UTC timestamp,
    # and count_toward_paywall=True (Day-3 safety stack will flip this
    # to False for safety-blocked replies).
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return MessageDto(
        id=str(uuid4()),
        conversation_id=request.conversation_id,
        role="assistant",
        content=STUB_CONTENT,
        media_urls=None,
        client_message_id=None,
        created_at=now_iso,
        count_toward_paywall=True,
    )


# ===========================================================================
# RELATED FILES:
#   main.py            — imports + mounts `router` on the FastAPI app
#   config.py          — `enable_run_turn_stub` setting + `environment` tag
#   models/turn.py     — RunTurnRequest + MessageDto Pydantic models
#   ../tests/test_run_turn.py
#                      — happy + error path coverage for this handler
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                      — chat-ai MessageDto parity contract (the source
#                        of truth for response shape)
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                      — internal RPC surface. Currently shows older
#                        SSE shape; DEP raised in cross-session-deps.
#   ../../.claude/agents/session-4-orchestrator.md
#                      — Day-2 plan + JSON-not-SSE directive that this
#                        file implements
# ===========================================================================
