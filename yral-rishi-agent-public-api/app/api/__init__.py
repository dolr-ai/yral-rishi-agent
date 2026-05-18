# ---------------------------------------------------------------------------
# app/api/ — public HTTP API surface for yral-rishi-agent-public-api.
#
# ⭐ START HERE: this package groups the route handlers + DTOs + the
# response envelope that mobile clients see when they POST to
# agent.rishi.yral.com. The package contains:
#
#   envelope.py         — ApiResponse[T] generic (the wrapper EVERY
#                         endpoint returns; matches what mobile already
#                         parses per the contract)
#   errors.py           — error code strings + helper for building
#                         envelope-shaped error responses
#   dtos.py             — Pydantic models for the response payloads
#                         (MessageDto, ConversationDto, InfluencerDto,
#                         ChatAccessDataDto) — copied 1:1 from
#                         interface-contracts/00-api-contract.md
#   feature_flag.py     — FastAPI dependency that gates Day-2 placeholder
#                         handlers behind the
#                         enable_session_3_phase_1_day_2_placeholder_responses
#                         config flag so production cannot serve stubs
#   chat_routes.py      — every /api/v1/chat/* + /api/v2/chat/* endpoint
#   influencer_routes.py — read set of /api/v1/influencers/* (create flow
#                         + admin endpoints land Day 6-7 parity sprint)
#   health_routes.py    — /health/{live,ready,deep} per F9 (local bridge
#                         in this spawned copy; DEP raised to Session 2
#                         to mirror in the template)
#
# WHO READS THIS PACKAGE?
#   - app/main.py wires every router into the FastAPI app at startup
#   - tests/contract/ asserts the envelope + DTO shape match the locked
#     contract per A8 + the contract doc
#   - Sessions 4 + 5 cross-reference the DTO definitions when implementing
#     their internal RPC handlers so the shapes match end-to-end
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Empty package marker. Submodules import what they need explicitly.

# ===========================================================================
# RELATED FILES:
#   app/main.py
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
# ===========================================================================
