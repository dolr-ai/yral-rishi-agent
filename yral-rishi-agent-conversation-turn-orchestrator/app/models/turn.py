# ---------------------------------------------------------------------------
# turn.py — Pydantic models for the run_turn RPC (Session 3 → orchestrator).
#
# ⭐ START HERE: two models live here.
#
#   1. `RunTurnRequest` — what Session 3's public-api sends to the
#      orchestrator's `POST /v1/turn` endpoint. Body carries the
#      conversation_id (which the orchestrator uses to look up the
#      user_id + ai_influencer_id via the conversation row) plus the
#      user_message text, the multi-modal `media_urls` per A8, the
#      optional per-request `influencer_id` (added in PR-B1 of the
#      3-PR per-request wiring plan — when set, overrides the
#      `day_5_placeholder_ai_influencer_id` env-var fallback in
#      `app/run_turn.py`; the env fallback continues until PR-B3
#      makes this field required), and the `client_message_id`
#      user-msg dedup hint. Authentication identity + tracing IDs
#      come via HTTP headers (X-User-Id, X-Idempotency-Key,
#      X-Request-Id), not the body.
#
#   2. `MessageResponse` — the response shape the orchestrator returns.
#      BYTE-IDENTICAL to chat-ai's `MessageResponse` from the parity
#      contract at `interface-contracts/00-api-contract.md`. Session 3's
#      public-api wraps this in the `ApiResponse<T>` envelope before
#      returning to the mobile client; the orchestrator itself returns
#      the naked `MessageResponse` over the internal RPC.
#
# WHY THE TYPE IS NAMED `MessageResponse`, NOT `MessageDto`
# Per Rishi's 2026-05-19 morning decision: "DTO" is not on the B2
# allowed-abbreviation list + English-naming applies to Python class
# names, not only JSON field names. Coordinator-owned PR #98 renamed
# the contract reference to `MessageResponse` at the same time. The
# JSON wire shape is UNCHANGED — only the Python class identifier
# moved.
#
# WHY PYDANTIC v2 + Field VALIDATORS?
# pydantic 2.10.5 (pinned in pyproject.toml) gives:
#   - Typed parsing at the FastAPI layer (422 on bad input — required by
#     test_run_turn.py's error-path tests).
#   - Schema export → the OpenAPI doc Session 3's public-api consumes
#     stays in sync without hand-written JSON-schema files.
#   - `min_length=1` on user_message + conversation_id rejects empty
#     strings without per-route validation code.
#
# WHY NOT INHERIT FROM A SHARED `models` PACKAGE IN
# `shared-library-code-used-by-every-v2-service/`?
# Per A2.1 — premature abstraction. Once Session 3 ALSO needs the
# `MessageResponse` shape (Day 4+), the coordinator can promote this
# file to the shared lib and both services import. For Day 2 it lives
# here so Session 4 can iterate the shape without coordinator gating
# per change.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `Literal` lets us pin the `role` field to the two-value enum
# ("user" / "assistant") without a separate StrEnum import.
from typing import Literal

# `BaseModel` is the Pydantic class every request/response model
# inherits from — it gives us typed parsing + validation at the
# FastAPI route boundary. `Field` lets us attach validators
# (`min_length=1`) without writing per-field validator methods.
from pydantic import BaseModel, Field


# ===========================================================================
# Request model
# ===========================================================================


class RunTurnRequest(BaseModel):
    """Body of `POST /v1/turn` from Session 3's public-api.

    WHAT: identifies WHICH conversation gets the new turn + carries the
          user's typed message, optional attachment URLs (multi-modal
          parity per A8), and the optional client-message dedup hint.
    WHEN: deserialised by FastAPI on every run_turn call.
    WHY:  decoupling identity (HTTP headers) from intent (body fields)
          matches the public-api contract pattern + lets the orchestrator
          treat the body as pure content while the headers carry trust.
    """

    # The conversation row's UUID. The orchestrator joins on this to find
    # user_id + ai_influencer_id (the latter feeds Soul File lookup).
    # `min_length=1` rejects empty strings before the handler runs.
    conversation_id: str = Field(min_length=1)

    # The raw text the user typed. This is PII per H6 — NEVER log it in
    # full. Logging this field's LENGTH is fine; the value is not.
    # `min_length=1` rejects empty strings (matches chat-ai behaviour).
    user_message: str = Field(min_length=1)

    # Multi-modal attachment URLs the user attached (images / audio /
    # video). REQUIRED by A8 multi-modal parity — public-api forwards
    # these inline so the orchestrator doesn't pay a second DB read per
    # turn. `None` when the user message has no attachments.
    media_urls: list[str] | None = None

    # Optional per-request AI Influencer UUID — overrides the
    # `day_5_placeholder_ai_influencer_id` env-var fallback in
    # `app/run_turn.py` for the Soul File Layer-3 lookup. When None
    # (field absent from request body entirely), the orchestrator
    # falls back to the env var (the Day-5 single-influencer
    # behaviour PR-B1 preserves for backwards-compatibility). PR-B1
    # of the 3-PR per-request wiring plan adds this optional field;
    # PR-B2 (Session 3) starts forwarding the per-chat influencer_id
    # from public-api; PR-B3 (Session 4) removes the env fallback +
    # makes this field required. Optional for now so PR-B1 ships
    # without a contract break.
    #
    # `min_length=1` rejects the present-but-explicit-blank case
    # (round-2 fixup per Codex CONCERN on PR #131). Without it, a
    # request body that explicitly sets `influencer_id=""` would
    # silently fall through the Python `or` short-circuit in the
    # resolver + route to the env placeholder. A wiring bug in
    # Session 3's public-api forwarding logic (e.g. forwarding the
    # wrong header, sending an empty string when a field was unset,
    # mis-serialising an unauthenticated user's request) would
    # never surface — all the affected chat traffic would silently
    # land on the single placeholder influencer instead of 422-ing
    # with a loud validation error. `default=None` still represents
    # "field omitted entirely" + routes to the env fallback (the
    # PR-B1 backwards-compatibility intent); `min_length=1` only
    # fires when the caller serialised a present-but-empty string
    # into the body. PR-B3 will tighten this further to required.
    influencer_id: str | None = Field(default=None, min_length=1)

    # Optional client-side dedup id the mobile app may attach to the
    # user message. The orchestrator echoes it onto persisted-user-msg
    # traces (Day-5+); assistant replies do NOT carry one.
    client_message_id: str | None = None


# ===========================================================================
# Response model — BYTE-IDENTICAL to chat-ai's MessageResponse per the
# parity contract at interface-contracts/00-api-contract.md
# ===========================================================================


class MessageResponse(BaseModel):
    """The orchestrator's response — one chat message row from chat-ai's
    schema, mirrored byte-for-byte per A8 + A16.

    WHAT: the persisted message that represents the assistant's reply.
          Fields match chat-ai's existing JSON exactly so mobile clients
          (which Session 3 forwards this to inside ApiResponse) see no
          schema delta during the parity window.
    WHEN: returned from `POST /v1/turn`. Session 3 wraps it in
          ApiResponse{success=true, msg='OK', error=null, data=...}.
    WHY:  CONSTRAINTS A8 + A16 — feature parity HARD constraint; no
          silent regressions in field names / types / nullability.
    """

    # UUID. Today (Day 2) the stub generates a fresh UUID per call so
    # callers can store + dedupe; once the real LLM lands (Day 5) the
    # UUID is assigned at persistence time.
    id: str

    # The conversation this message belongs to. Echoes the request's
    # conversation_id so callers can correlate without re-parsing.
    conversation_id: str

    # `user` for messages the human sent; `assistant` for the LLM reply.
    # The orchestrator's run_turn always returns `assistant` (the user
    # message is persisted by public-api before the RPC fires).
    role: Literal["user", "assistant"]

    # The reply text. Day-2 stub returns the literal placeholder string;
    # Day-5 real LLM enablement replaces this with the model's output.
    content: str

    # Optional list of attachment URLs. Null in Day-2 stub; real Day-5+
    # responses may include generated images per the media-vault path.
    media_urls: list[str] | None = None

    # Optional client-side dedup key (chat-ai field). Public-api copies
    # X-Client-Message-Id into the persisted user-message row; the
    # assistant reply doesn't carry one — set null here.
    client_message_id: str | None = None

    # ISO8601 UTC timestamp, `YYYY-MM-DDTHH:MM:SSZ` shape. Matches what
    # chat-ai writes to Postgres + serialises to mobile.
    created_at: str

    # Whether this turn counts against the 50-msg paywall window per E7.
    # Day-2 stub returns True (every assistant reply counts toward the
    # paywall today; the real flag flips for safety-stack-blocked turns
    # in Day-3 once the H4/H5 middleware lands).
    count_toward_paywall: bool


# ===========================================================================
# RELATED FILES:
#   __init__.py    — package marker
#   ../run_turn.py — POST /v1/turn handler consuming these models
#   ../main.py     — mounts run_turn's router on the FastAPI app
#   ../config.py   — `enable_run_turn_stub` feature flag + `redis_url`
#   ../idempotency.py
#                  — Redis-backed F10 dedup; reads + writes serialised
#                    `MessageResponse` payloads keyed by user + key
#   ../../tests/test_run_turn.py
#                  — exercises both happy + error paths + the F10
#                    idempotency replay path against these models
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                  — chat-ai parity MessageResponse source-of-truth
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                  — internal RPC surface (Session 3 ↔ orchestrator);
#                    coordinator-owned. PR #98 (commit f708a49) renamed
#                    the contract reference to `MessageResponse` + added
#                    media_urls / client_message_id at the same time
#                    this fixup landed in code.
# ===========================================================================
