# ---------------------------------------------------------------------------
# test_gate_a1_send_message_smoke.py — Gate A1 per-PR PUBLIC-API
# integration SMOKE check for the send-message hot path.
#
# ⭐ INTENTIONAL SCAFFOLD — implementation lands by Day 11-13
# (Phase 1 parity smoke target) per PR #145 Gate A1 acceptance
# criteria. CI-coverage gap on the new public-api → user-memory
# behavior is INTENTIONAL until the implementation step (Codex
# PR #141 round-6 CONCERN 1 informational acknowledgment). The
# contract-level unit tests in
# `tests/contract/test_orchestrator_proxy.py` (including the
# trust-boundary forgery-rejection test + the round-7 id /
# user_id verification checks) cover the public-api side of the
# boundary at the unit-test tier in the meantime.
#
# Each test below carries the Gate A1 acceptance criterion in its
# docstring; module-level pytest.skip keeps CI green until the
# implementation step lands.
#
# GATE A1 SPEC (verbatim from PR #145 ratification doc — section
# "Gate A1 — per-PR PUBLIC-API integration-test SMOKE check" in
# yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md):
#
#   - Test stubs ALL downstream services (user-memory, orchestrator,
#     yral-billing) via ephemeral-port HTTP fakes — NOT real
#     testcontainers (Codex PR #145 round-8 CONCERN: cross-session
#     test ownership leakage)
#   - Asserts the public-api → user-memory call is INSTRUMENTED with
#     a Langfuse span (in-process span exporter on public-api side)
#   - Asserts the RPC contract shape: request path + 4 headers
#     (X-User-Id, X-Internal-Caller, X-Trace-Id, X-Request-Id) +
#     response body matches the proposed shape
#   - Response Pydantic model parses cleanly (contract-level
#     validation, not implementation peek)
#   - Asserts envelope mapping to ApiResponse<MessageDto> wire shape
#     (parity-locked per 00-api-contract.md:35)
#   - Asserts timeout/error/5xx behavior with envelope-shaped 503
#   - SMOKE check only at PR-CI tier (no p95 thresholds per J2)
#
# WHY 5 TESTS?
# Each test isolates one acceptance criterion so a failure points
# at exactly one contract obligation. Bundling would obscure which
# bit of the boundary regressed.
#
# RELATED FILES:
#   conftest.py — ephemeral-port fakes + Langfuse span exporter
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#     — Gate A1 spec
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#     — ApiResponse<MessageDto> envelope wire shape (line 35)
# ---------------------------------------------------------------------------

import pytest

# Module-level skip keeps CI green while the scaffold lives in tree.
# Day-12-13 implementation step removes this line + fills in each
# test body using the conftest fixtures.
pytestmark = pytest.mark.skip(
    reason=(
        "Gate A1 scaffold — implementation queued for Day 12-13 "
        "Phase 1 parity smoke target per PR #145 ratified architecture"
    )
)


def test_send_message_smoke_envelope_maps_to_api_response_message_dto():
    """Envelope-mapping parity-lock per 00-api-contract.md:35.

    WHAT: POST /api/v1/chat/conversations/{id}/messages with valid
          auth + all 3 downstream fakes returning canonical happy-
          path responses; assert response body matches the locked
          ApiResponse<MessageDto> envelope shape (success=True,
          data=MessageDto, error=None, request_id present).
    WHEN: every PR that touches the send-message hot path (the
          coordinator-owned merge-protection rule selects which
          PRs).
    WHY:  contract parity-lock — the envelope wire shape is the
          public-api side of the mobile-app contract; a regression
          here breaks the Motorola debug APK without any unit-test
          signal (unit tests can pass while the envelope shape
          silently changes).
    """
    raise NotImplementedError("Gate A1 — Day 12-13 implementation step")


def test_send_message_smoke_user_memory_request_path_and_4_headers():
    """RPC request shape per PR #145 ratification line 304.

    WHAT: send-message request; user-memory fake records inbound
          request; assert path is GET /v1/conversations/{conv_id} +
          all 4 headers present and non-empty (X-User-Id,
          X-Internal-Caller, X-Trace-Id, X-Request-Id) + no request
          body (GET has no body per the contract).
    WHEN: every PR; this is the load-bearing observable surface
          between public-api and user-memory.
    WHY:  drops in the 4 headers per the "Authentication between
          services" section at the top of 01-internal-rpc-contracts.md;
          a missing header would let a downstream service see an
          untraceable cross-service call (E1 latency breakdowns +
          incident response both depend on the 4-header set).
    """
    raise NotImplementedError("Gate A1 — Day 12-13 implementation step")


def test_send_message_smoke_user_memory_response_model_parses_cleanly():
    """Contract-level Pydantic validation per PR #145 ratification line 304.

    WHAT: user-memory fake emits the documented JSON response
          shape; assert public-api's response parser successfully
          validates against the ConversationResponse Pydantic
          model — no schema drift, no field-name typo, no
          accidentally-dropped key.
    WHEN: every PR; this is the contract-level validation, not
          implementation peek (the spec explicitly distinguishes
          the two).
    WHY:  Pydantic-model-based parsing means an evolved schema on
          either side surfaces as a parse error at the boundary
          instead of a silent KeyError deep in the chat handler.
    """
    raise NotImplementedError("Gate A1 — Day 12-13 implementation step")


def test_send_message_smoke_user_memory_call_carries_langfuse_span():
    """Langfuse-span instrumentation per PR #145 ratification line 303.

    WHAT: in-process Langfuse span exporter on the public-api side
          captures the spans emitted during one send-message turn;
          assert the public-api → user-memory call has a Langfuse
          span attached + the span attributes include the request
          path and the response status.
    WHEN: every PR that touches the send-message hot path or any
          public-api → user-memory code path.
    WHY:  E1's 50%-faster latency target relies on Langfuse traces
          for the cross-service-call breakdown; without
          instrumentation the latency observability story breaks
          silently. SMOKE-level only — no p95 thresholds here per
          J2 (the controlled latency gates A2-PR + A2-NIGHTLY +
          B carry the real p95 enforcement).
    """
    raise NotImplementedError("Gate A1 — Day 12-13 implementation step")


def test_send_message_smoke_user_memory_timeout_maps_to_503_envelope():
    """Timeout / error / 5xx → envelope-shaped 503 per coordinator scope.

    WHAT: user-memory fake delays beyond the client timeout (OR
          returns a 5xx); assert public-api returns HTTP 503 +
          envelope shape (success=False, error="service_unavailable",
          data populated with the dependency signal) AND that the
          orchestrator + yral-billing fakes receive ZERO requests
          (the user-memory failure short-circuits the request
          before orchestrator/billing are even consulted).
    WHEN: every PR; this is the failure-mode contract that protects
          mobile from a degraded backend leaking unexpected error
          shapes.
    WHY:  envelope-shaped 503 is the parity-locked failure
          contract; a regression here looks like a mobile-side
          parse error during a Session 5 incident, which is the
          worst time for the contract to drift.
    """
    raise NotImplementedError("Gate A1 — Day 12-13 implementation step")
