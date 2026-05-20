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
# `Final` marks the Day-5 helper's user_segment constant as immutable.
from typing import Annotated, Final

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
# overwrites the in-progress lock with the completed response;
# `release_in_progress_lock` (added round-5 per Codex BLOCKER 96-A)
# is the failure-path cleanup that prevents an exception mid-handler
# from holding the dedup lock for the full 24h F10 window.
from app.idempotency import (
    acquire_or_check,
    compute_idempotency_key,
    compute_request_fingerprint,
    mark_complete,
    release_in_progress_lock,
)

# Pydantic models the route declares — FastAPI uses these for
# request parsing + response serialisation. `MessageResponse` is
# BYTE-IDENTICAL to chat-ai's MessageResponse per A8 + A16.
from app.models.turn import MessageResponse, RunTurnRequest

# Day-5 — abstract LlmClient + the typed exception shapes the handler
# catches + maps to 502/504 envelopes. Per A10 the orchestrator only
# depends on the interface; Day 6+ provider-routing matrix swaps the
# concrete client without touching this file. The Gemini concrete
# client is the only one wired today.
from app.llm_client import (
    GeminiClient,
    LlmClient,
    LlmClientTimeoutError,
    LlmClientUpstreamError,
    get_default_llm_client,
)

# Day-5 — Soul File RPC client + the typed exception shapes. The
# `get_soul_file_client()` accessor returns the lifespan-managed
# singleton; the handler catches the two exceptions + maps them to
# 404 / 503 envelopes.
from app.soul_file_client import (
    SoulFileInfluencerNotFoundError,
    SoulFileUpstreamError,
    get_soul_file_client,
)


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
# Day-5 helpers — real LLM reply generator + best-effort lock release
# ===========================================================================


async def _generate_real_llm_reply(
    *,
    request: RunTurnRequest,
    settings,
) -> str:
    """Run the Day-5 soul-file → LLM → reply pipeline + return the reply text.

    WHAT: fetches the layered prompt from the soul-file-library, then
          calls the default LLM client (Gemini today). Returns the
          assistant's reply text the caller wraps in MessageResponse.
    WHEN: called from the run_turn handler's `acquired` branch when
          `enable_run_turn_real_llm=True`.
    WHY:  encapsulates the Day-5 pipeline in one function so the
          handler stays readable + the same pipeline can be reused
          when Day 6+ adds the routing matrix. The Day-2 stub path
          stays a one-liner in the handler.

    Raises:
      SoulFileInfluencerNotFoundError — handler maps to 404 envelope.
      SoulFileUpstreamError           — handler maps to 503 envelope.
      LlmClientTimeoutError           — handler maps to 504 envelope.
      LlmClientUpstreamError          — handler maps to 502 envelope.
      RuntimeError                    — handler maps to 500 via the
                                        existing generic `except`.
    """
    # Hardcode user_segment="new" per the Day-5 directive verbatim.
    # User-segment tracking lands in a later phase; today's chat-turn
    # always reads the Layer-4 "new" row.
    user_segment_for_day_5: Final = "new"

    placeholder_influencer_id = settings.day_5_placeholder_ai_influencer_id
    if not placeholder_influencer_id:
        raise RuntimeError(
            "enable_run_turn_real_llm=True but "
            "day_5_placeholder_ai_influencer_id is empty. Set "
            "DAY_5_PLACEHOLDER_AI_INFLUENCER_ID to a seeded soul-file "
            "Layer-3 row's influencer_id (see soul-file-library's "
            "RUNBOOK for the seeded ids)."
        )

    # Soul-file lookup. Returns ComposedPrompt with layered_prompt +
    # version_pin + cache_hit. Errors propagate as typed exceptions
    # the handler catches.
    soul_file_client = get_soul_file_client()
    composed = await soul_file_client.compose(
        influencer_id=placeholder_influencer_id,
        user_segment=user_segment_for_day_5,
    )

    _log.info(
        "soul_file_compose_succeeded",
        extra={
            "conversation_id": request.conversation_id,
            "influencer_id": placeholder_influencer_id,
            "user_segment": user_segment_for_day_5,
            "version_pin": composed.version_pin,
            "cache_hit": composed.cache_hit,
            "layered_prompt_length": len(composed.layered_prompt),
        },
    )

    # LLM call. The default client is Gemini today; Day 6+ routing
    # picks a different concrete client based on the influencer +
    # safety stack decisions.
    llm_client = get_default_llm_client()
    llm_response = await llm_client.generate(
        prompt=composed.layered_prompt,
        user_message=request.user_message,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )

    _log.info(
        "llm_call_succeeded",
        extra={
            "conversation_id": request.conversation_id,
            "provider": llm_response.provider,
            "model": llm_response.model,
            "prompt_tokens": llm_response.prompt_tokens,
            "completion_tokens": llm_response.completion_tokens,
            "latency_milliseconds": llm_response.latency_milliseconds,
            "content_length": len(llm_response.content),
        },
    )

    return llm_response.content


async def _safely_release_lock(
    *,
    redis_key: str,
    request: RunTurnRequest,
) -> None:
    """Release the in-progress idempotency lock on a typed failure path.

    WHAT: wraps `release_in_progress_lock(...)` in best-effort logging
          so a Redis-down secondary failure doesn't drown out the
          original error.
    WHEN: called from each of the four typed-exception branches in
          the route handler (soul-file 404/503 + LLM 504/502) BEFORE
          returning the envelope response.
    WHY:  Codex round-5 BLOCKER 96-A established that a leaked
          in-progress lock blocks legitimate retries for 24h. Same
          rationale applies to the typed-error paths Day-5 introduces;
          this helper centralises the best-effort cleanup so each
          envelope-return path is one call.
    """
    try:
        await release_in_progress_lock(redis_key)
    except Exception as release_failure:
        _log.error(
            "idempotency_lock_release_failed_on_typed_error_path",
            extra={
                "conversation_id": request.conversation_id,
                "release_failure_type": type(release_failure).__name__,
            },
        )


# ===========================================================================
# Handlers
# ===========================================================================


@router.post(
    "/v1/turn",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Run one conversation turn (Day-5 real-LLM | Day-2 stub, F10-dedup, C11-Redis).",
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
    """Day-5 real-LLM run_turn RPC, behind two gates + atomic F10.

    WHAT: gates → idempotency-key header required check → atomic-acquire
          the in-progress lock → on `replay_done` return cached payload,
          on `fingerprint_mismatch` return 409 envelope, on
          `in_flight_timeout` return 503 envelope, on `acquired`:
            * if `enable_run_turn_real_llm=True` (Day-5 path) call
              soul-file → LLM → wrap MessageResponse;
            * else (Day-2 stub path, kept for diagnostics) use
              the STUB_CONTENT literal;
          mark_complete + return.
    WHEN: invoked by Session 3's public-api on every chat-message turn.
    WHY:  Day-5 milestone is "the AI actually responds". The real-LLM
          path runs the soul-file lookup + Gemini call + wraps the
          reply in the chat-ai-parity MessageResponse shape (per A8).
          Day-2 stub stays accessible for non-prod diagnostics per the
          agent definition.

    Day-5 typed-failure-path envelopes (all release the in-progress
    lock so retries can start fresh):
      * 404 `influencer_not_found`         — soul-file 404
      * 503 `soul_file_upstream_unavailable` — soul-file 5xx/timeout
      * 504 `llm_upstream_timeout`         — LLM exceeded 30s budget
      * 502 `llm_upstream_error`           — LLM API error

    The `request_id` header is PASSED THROUGH (read but not used in
    the response path); Day-6+ wires it into Langfuse trace metadata
    once the safety stack relands.
    """
    # -----------------------------------------------------------------------
    # GATES (must run BEFORE Redis touches — a production env should
    # never ALSO be reading/writing the idempotency cache).
    # -----------------------------------------------------------------------
    settings = get_settings()

    # Gate 1: unconditional production safety net. Both Day-2 stub +
    # Day-5 real-LLM paths refuse production traffic — production
    # cutover requires A6's typed YES, not a flag flip.
    if settings.environment == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="run_turn disabled in production — A6 cutover required.",
        )

    # Gate 2: at least one of (real LLM | stub) must be enabled.
    # Day-5 introduces the real-LLM flag; the Day-2 stub flag stays
    # accessible for diagnostics per the agent definition. Both off
    # means the handler 503s — defence-in-depth against a half-
    # configured non-prod environment.
    if not (
        settings.enable_run_turn_real_llm or settings.enable_run_turn_stub
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "run_turn disabled — set ENABLE_RUN_TURN_REAL_LLM=true "
                "(Day-5 path) or ENABLE_RUN_TURN_STUB=true (Day-2 "
                "diagnostic stub) to enable."
            ),
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
    # STUB PROCESSING — wrapped in try/except per Codex round-5 BLOCKER
    # 96-A so a handler-side exception releases the in-progress lock
    # instead of leaving it held for the full 24h F10 dedup window.
    # -----------------------------------------------------------------------
    # WHY THE try BLOCK STARTS HERE (not earlier):
    #   The lock was acquired by the `acquire_or_check(...)` call
    #   above. Everything BEFORE that call (gates, header checks, key
    #   construction, fingerprint) cannot hold a lock — there's
    #   nothing for the failure path to release. The try block only
    #   needs to cover the post-acquire window, which is exactly the
    #   "handler did the work but never marked complete" failure mode
    #   Codex flagged. Day-5+ LLM-client errors fall into this same
    #   window; the same cleanup applies.
    #
    # WHY release ONLY on the failure path:
    #   On the happy path `mark_complete(...)` overwrites the lock
    #   with the `done`-state payload that every concurrent waiter +
    #   every subsequent retry within the 24h F10 window expects to
    #   serve from. Releasing AFTER `mark_complete` would erase the
    #   cached response + defeat F10's "byte-identical replay"
    #   contract. So `release_in_progress_lock` runs strictly inside
    #   the `except` branch.
    #
    # WHY re-raise (not swallow + return 500 ourselves):
    #   Letting the exception propagate to FastAPI's default
    #   exception handler gives us the same 500 envelope shape every
    #   other unhandled-exception path produces + Sentry capture +
    #   structured-log traceback for free. Swallowing + returning a
    #   bespoke JSONResponse here would create a divergent error
    #   surface the alerting + tracing rules would have to learn
    #   about. Per A2.1: keep the minimum addition that fixes the
    #   bug.
    try:
        # ISO8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`. Matches chat-ai's wire
        # shape; renamed from the round-2 `now_iso` per Codex round-3
        # BLOCKER 3 (B2 disallows the `iso` abbreviation).
        current_utc_timestamp_text = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )

        # -------------------------------------------------------------------
        # PATH SELECT — Day-5 real LLM if its flag is on; else Day-2 stub.
        # -------------------------------------------------------------------
        # NOTE: Safety stack (H5/H4/A10) is being re-landed in a parallel
        # coordinator PR (replacement for auto-closed PR #100). Once that
        # merges, a small follow-up PR wires the safety middleware in
        # front of this LLM call. Day-5 staging-cluster scope is acceptable
        # without safety because no production traffic reaches this code
        # yet (rishi-4/5/6 only; production stays on chat-ai.rishi.yral.com).
        if settings.enable_run_turn_real_llm:
            # Day-5 real-LLM path. Reads the Day-5 placeholder
            # ai_influencer_id from settings (Day-6+ replaces this with
            # the conversation-row lookup per the directive); fetches
            # the composed soul-file prompt; calls Gemini; wraps the
            # reply in MessageResponse.
            #
            # The five potential failure modes here have envelope
            # mappings inside this same try-block — see the inner
            # `except` chain below the response build.
            assistant_reply_content = await _generate_real_llm_reply(
                request=request,
                settings=settings,
            )
        else:
            # Day-2 stub path — kept for diagnostics per the agent
            # definition. The `enable_run_turn_stub=True` gate above
            # is the only way to reach this branch when the real-LLM
            # flag is off.
            assistant_reply_content = STUB_CONTENT

        response = MessageResponse(
            id=str(uuid4()),
            conversation_id=request.conversation_id,
            role="assistant",
            content=assistant_reply_content,
            media_urls=None,
            client_message_id=None,
            created_at=current_utc_timestamp_text,
            count_toward_paywall=True,
        )

        # Overwrite the in-progress lock with the completed response.
        # Every concurrent waiter that's polling will harvest this
        # payload on its next GET. `model_dump(mode="json")` serialises
        # Pydantic-typed values to JSON-native primitives so the
        # `json.dumps` inside `mark_complete` always succeeds.
        await mark_complete(
            redis_key=redis_key,
            fingerprint=request_fingerprint,
            response_payload=response.model_dump(mode="json"),
        )

        return response
    except SoulFileInfluencerNotFoundError:
        # Caller-side misconfiguration: the configured Day-5 placeholder
        # ai_influencer_id has no L3 row in the soul-file-library. Map
        # to 404 envelope; release the lock so a retry after the operator
        # updates the setting starts fresh.
        await _safely_release_lock(redis_key=redis_key, request=request)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_api_response_envelope(
                error_code="influencer_not_found",
                message_text=(
                    "Configured AI Influencer has no soul-file Layer-3 row "
                    "in the soul-file-library. Operator action: update "
                    "DAY_5_PLACEHOLDER_AI_INFLUENCER_ID to a seeded id."
                ),
            ),
        )
    except SoulFileUpstreamError:
        # Soul-file-library is down or returning unexpected shapes.
        # Map to 503; release lock so a retry after the upstream recovers
        # starts fresh.
        await _safely_release_lock(redis_key=redis_key, request=request)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_api_response_envelope(
                error_code="soul_file_upstream_unavailable",
                message_text=(
                    "soul-file-library is currently unavailable; retry "
                    "after a short backoff."
                ),
            ),
        )
    except LlmClientTimeoutError:
        # LLM provider exceeded the 30s budget. Map to 504 per the
        # directive's "Bail with envelope-shaped 504 on timeout"
        # contract; release the lock so a client retry can route to a
        # different provider (Day 6+) without waiting on the dangling
        # lock.
        await _safely_release_lock(redis_key=redis_key, request=request)
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content=_api_response_envelope(
                error_code="llm_upstream_timeout",
                message_text=(
                    "LLM provider exceeded the 30s response budget; retry "
                    "after a short backoff."
                ),
            ),
        )
    except LlmClientUpstreamError:
        # LLM provider returned a non-success status / surfaced an API
        # error (rate-limit, auth, quota, model unavailable). Map to
        # 502 envelope; release lock.
        await _safely_release_lock(redis_key=redis_key, request=request)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_api_response_envelope(
                error_code="llm_upstream_error",
                message_text=(
                    "LLM provider returned an unexpected error; retry "
                    "after a short backoff."
                ),
            ),
        )
    except Exception:
        # Log + release the in-progress lock so a client retry with
        # the same X-Idempotency-Key gets a fresh `acquired` decision
        # within the 24h window (instead of the 503 in_flight_timeout
        # it would have hit if we left the lock dangling).
        # `release_in_progress_lock` is best-effort — if Redis itself
        # is down, the original handler exception still propagates +
        # the lock will expire at the 24h F10 TTL.
        _log.warning(
            "run_turn_handler_failed_releasing_idempotency_lock",
            extra={
                "conversation_id": request.conversation_id,
            },
        )
        try:
            await release_in_progress_lock(redis_key)
        except Exception as release_failure:
            # Surface the release failure so an operator notices the
            # lock will stick for 24h. Original handler exception
            # still re-raises so the caller sees a 500.
            _log.error(
                "idempotency_lock_release_failed",
                extra={
                    "conversation_id": request.conversation_id,
                    "release_failure_type": type(release_failure).__name__,
                },
            )
        raise


# ===========================================================================
# RELATED FILES:
#   main.py            — imports + mounts `router`; lifespan init/close
#                        Redis + soul-file client + default LLM client
#   config.py          — `enable_run_turn_real_llm` (Day-5) +
#                        `enable_run_turn_stub` (Day-2 diagnostic) +
#                        `gemini_api_key` + `llm_temperature` +
#                        `llm_max_tokens` + `soul_file_library_base_url` +
#                        `day_5_placeholder_ai_influencer_id` settings
#   idempotency.py     — F10 atomic-dedup helpers (acquire_or_check +
#                        mark_complete + release_in_progress_lock +
#                        compute_request_fingerprint)
#   llm_client/        — A10 abstract LLM client interface + Gemini
#                        provider + lifespan singleton accessor
#   soul_file_client.py — httpx-based Soul File Library RPC client +
#                        the two typed exceptions this handler maps to
#                        404 / 503 envelopes
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
