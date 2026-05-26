# ---------------------------------------------------------------------------
# conftest.py — fixtures for Gate A1 public-api integration SMOKE tests.
#
# SCAFFOLD ONLY — implementation of the ephemeral-port HTTP fake
# fixtures (user-memory, orchestrator, yral-billing) is deferred to
# Day 12-13 per PR #145 architectural ratification. Each fixture
# below is declared with a clear acceptance contract in its
# docstring so the implementation step can fill bodies without
# re-discovering the spec.
#
# WHY EPHEMERAL-PORT FAKES (NOT REAL TESTCONTAINERS)?
# Per PR #145 round-8 CONCERN: real testcontainers for downstream
# services couple public-api's CI to other-session implementations
# (Session 4's orchestrator, Session 5's user-memory). Cross-session
# test-ownership leakage. Public-api's CI exercises the HTTP CONTRACT
# (path + headers + response shape) via in-test HTTP fakes; the REAL
# downstream-service tests live in each owner-session's gate
# (Gate A_user_memory in Session 5, Gate A_orchestrator in Session 4).
#
# LIBRARY CHOICE — pytest-httpserver vs respx vs aiohttp.web?
# Spec says any of the three. Implementation step picks one; today's
# scaffold is library-agnostic so the fixture contract is what
# determines correctness, not the library.
#
# RELATED FILES:
#   tests/integration/test_gate_a1_send_message_smoke.py
#     — the 5 scaffold tests that consume these fixtures
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#     — Gate A1 spec (section "Gate A1 — per-PR PUBLIC-API
#       integration-test SMOKE check")
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def user_memory_fake_server():
    """Ephemeral-port HTTP fake for user-memory-service.

    WHAT: stands up a local HTTP server on an ephemeral port that
          accepts GET /v1/conversations/{id}, records the inbound
          request (path + headers + body), and emits a canonical
          ConversationResponse JSON.
    WHEN: every Gate A1 send-message smoke test that requires the
          public-api → user-memory call to land somewhere observable.
    WHY:  enables contract-level assertions (path + 4 headers +
          response shape) without depending on Session 5's running
          implementation.
    """
    pytest.skip("Gate A1 scaffold — fixture body deferred to Day 12-13 implementation step per PR #145 ratification")


@pytest.fixture
def orchestrator_fake_server():
    """Ephemeral-port HTTP fake for the orchestrator's run_turn RPC.

    WHAT: stands up a local HTTP server that accepts POST /v1/turn,
          records the inbound request, and emits a canonical
          MessageDto JSON.
    WHEN: every Gate A1 send-message smoke test that needs the
          full request chain to complete.
    WHY:  enables envelope-mapping assertions (ApiResponse<MessageDto>
          parity-lock per 00-api-contract.md:35) without depending on
          Session 4's running implementation.
    """
    pytest.skip("Gate A1 scaffold — fixture body deferred to Day 12-13 implementation step per PR #145 ratification")


@pytest.fixture
def yral_billing_fake_server():
    """Ephemeral-port HTTP fake for yral-billing (Phase-1 forward-compat).

    WHAT: stands up a local HTTP server matching the yral-billing
          /google/chat-access/check shape referenced in
          app/api/response_models.py.
    WHEN: every Gate A1 smoke test once public-api takes a real
          dependency on yral-billing (NOT YET — chat handler does
          not call billing today; the fake is wired now so the test
          shape is ready when billing-precheck lands).
    WHY:  coordinator scope ask: scaffold accommodates all 3
          downstream services so the Day-12-13 implementation step
          doesn't reshape the fixture surface.
    """
    pytest.skip("Gate A1 scaffold — fixture body deferred to Day 12-13 implementation step per PR #145 ratification")


@pytest.fixture
def langfuse_in_process_span_exporter():
    """In-process Langfuse span exporter on the public-api side.

    WHAT: installs an in-process span exporter that records every
          Langfuse span emitted during the test request, so tests
          can assert the public-api → user-memory call carries an
          instrumented span (PR #145 line 303).
    WHEN: every Gate A1 test that asserts cross-service-call
          observability.
    WHY:  E1's 50%-faster latency target relies on Langfuse traces
          for the cross-service-call breakdown; this exporter is
          how the Gate A1 SMOKE check proves instrumentation didn't
          regress in this PR.
    """
    pytest.skip("Gate A1 scaffold — fixture body deferred to Day 12-13 implementation step per PR #145 ratification")
