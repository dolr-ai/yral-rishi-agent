# ---------------------------------------------------------------------------
# test_soul_file_client.py — Day-5 Soul File RPC client unit tests.
#
# ⭐ START HERE: this file proves the RPC client's three behaviours:
#   1. Happy 200 → returns a ComposedPrompt with all three contract
#      fields populated; the GET request shape matches the locked
#      contract (path + query params).
#   2. 404 → raises SoulFileInfluencerNotFoundError. run_turn.py maps
#      this to a 404 `influencer_not_found` envelope.
#   3. 5xx + timeout + unparseable body → raises SoulFileUpstreamError.
#      run_turn.py maps this to a 503 `soul_file_upstream_unavailable`
#      envelope.
#
# WHY MOCK httpx (NOT THE TRANSPORT)
# Per A2.1: httpx is the boundary that matters. `httpx.AsyncClient.get`
# is the documented async API; mocking the transport layer underneath
# would couple tests to httpx internals.
#
# WHY NO TIMING-BASED TEST OF THE 5s TIMEOUT
# Same rationale as the Gemini timeout test — raising
# httpx.TimeoutException from the mock directly exercises the same
# except branch with zero wall-clock dependency.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `unittest.mock` — AsyncMock for the async coroutine method
# we substitute on the SoulFileClient's internal httpx instance.
from unittest.mock import AsyncMock, MagicMock

# `pytest` itself — for the `@pytest.mark.asyncio` decorator + the
# `MonkeyPatch` type for stubbing internals.
import pytest

# `httpx` — for constructing the fake Response objects + the typed
# exception classes (TimeoutException, NetworkError) we raise from
# the mock to exercise the upstream-error branch.
import httpx

# Imports under test.
from app.soul_file_client import (
    ComposedPrompt,
    SoulFileClient,
    SoulFileInfluencerNotFoundError,
    SoulFileUpstreamError,
)


# Shared test fixtures — a canonical influencer id + segment used
# across every test so a regression on the query-param shape shows
# up consistently.
_TEST_INFLUENCER_ID: str = "11111111-2222-3333-4444-555555555555"
_TEST_USER_SEGMENT: str = "new"
_TEST_BASE_URL: str = "http://yral-rishi-agent-soul-file-library:8000"


def _build_client_with_mocked_http(*, mocked_http: MagicMock) -> SoulFileClient:
    """Construct SoulFileClient + swap its internal httpx for a mock.

    WHAT: builds a real SoulFileClient (so the docstrings + helpers
          stay exercised) then replaces `_http` with the test's mock.
    WHEN: called by every test that needs to stub upstream responses.
    WHY:  per A2.1 we avoid building a test-only "fake" parallel
          SoulFileClient; substituting the httpx attribute is the
          smallest possible test seam.
    """
    client = SoulFileClient(base_url=_TEST_BASE_URL, call_timeout_seconds=5.0)
    client._http = mocked_http  # noqa: SLF001
    return client


# ===========================================================================
# Happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_returns_typed_composed_prompt_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: on a 200 response with the contract shape, .compose()
          returns ComposedPrompt with layered_prompt / version_pin /
          cache_hit populated.
    WHEN: every successful chat turn (the soul-file library returns
          the 4-layer composed prompt).
    WHY:  proves the wire shape matches the contract at
          `interface-contracts/01-internal-rpc-contracts.md` verbatim.
          A regression that drops a field would cascade into the LLM
          call missing system_instruction.
    """
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={
        "layered_prompt": "the 4-layer composed prompt under test",
        "version_pin": "abcdef0123456789",
        "cache_hit": False,
    })

    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(return_value=fake_response)

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    result = await client.compose(
        influencer_id=_TEST_INFLUENCER_ID,
        user_segment=_TEST_USER_SEGMENT,
    )

    assert isinstance(result, ComposedPrompt)
    assert result.layered_prompt == "the 4-layer composed prompt under test"
    assert result.version_pin == "abcdef0123456789"
    assert result.cache_hit is False


@pytest.mark.asyncio
async def test_compose_passes_influencer_id_and_user_segment_as_query_params() -> None:
    """WHAT: the GET request hits `/composed-prompt` with
          `influencer_id` + `user_segment` as query params (per the
          contract at PR #98 verbatim).
    WHEN: every chat turn.
    WHY:  contract-shape regression catch. A typo in the param name
          would silently break the soul-file-library's routing.
    """
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={
        "layered_prompt": "x",
        "version_pin": "y",
        "cache_hit": False,
    })

    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(return_value=fake_response)

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    await client.compose(
        influencer_id=_TEST_INFLUENCER_ID,
        user_segment=_TEST_USER_SEGMENT,
    )

    mocked_http.get.assert_awaited_once()
    call_args = mocked_http.get.await_args
    assert call_args.args[0] == "/composed-prompt"
    assert call_args.kwargs["params"] == {
        "influencer_id": _TEST_INFLUENCER_ID,
        "user_segment": _TEST_USER_SEGMENT,
    }


# ===========================================================================
# 404 path
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_raises_influencer_not_found_on_404() -> None:
    """WHAT: a 404 upstream response raises
          SoulFileInfluencerNotFoundError.
    WHEN: the configured ai_influencer_id has no L3 row in the
          soul-file-library.
    WHY:  run_turn.py maps this exception to a 404 envelope with
          error_code `influencer_not_found`, which is distinguishable
          from "soul-file is down" (503). A regression that conflated
          the two would muddle operator response: 404 = "fix your
          config"; 503 = "investigate soul-file-library health".
    """
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 404

    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(return_value=fake_response)

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    with pytest.raises(SoulFileInfluencerNotFoundError):
        await client.compose(
            influencer_id=_TEST_INFLUENCER_ID,
            user_segment=_TEST_USER_SEGMENT,
        )


# ===========================================================================
# Upstream-error paths
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_raises_upstream_error_on_timeout() -> None:
    """WHAT: an httpx.TimeoutException raises SoulFileUpstreamError.
    WHEN: soul-file-library doesn't respond within the 5s budget.
    WHY:  run_turn.py maps this to a 503 envelope. Operator-side
          signal that the upstream is degraded (or the network in
          between is broken).
    """
    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(
        side_effect=httpx.TimeoutException("simulated timeout"),
    )

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    with pytest.raises(SoulFileUpstreamError):
        await client.compose(
            influencer_id=_TEST_INFLUENCER_ID,
            user_segment=_TEST_USER_SEGMENT,
        )


@pytest.mark.asyncio
async def test_compose_raises_upstream_error_on_5xx_status() -> None:
    """WHAT: a 503 (or any 5xx) upstream raises SoulFileUpstreamError.
    WHEN: soul-file-library is returning errors (DB down, container
          unhealthy).
    WHY:  same envelope mapping as the timeout — operator sees one
          consistent "soul-file degraded" signal across both failure
          modes.
    """
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 503

    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(return_value=fake_response)

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    with pytest.raises(SoulFileUpstreamError):
        await client.compose(
            influencer_id=_TEST_INFLUENCER_ID,
            user_segment=_TEST_USER_SEGMENT,
        )


@pytest.mark.asyncio
async def test_compose_raises_upstream_error_on_unparseable_body() -> None:
    """WHAT: a 200 with a body missing a contract field (e.g.
          version_pin) raises SoulFileUpstreamError.
    WHEN: upstream regression that drops a field — should never
          happen but the test asserts we fail safely if it does.
    WHY:  the alternative (KeyError propagating up the stack) would
          crash the orchestrator with a 500 instead of producing a
          clean envelope. SoulFileUpstreamError → 503 envelope is
          the right operator-visible signal.
    """
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 200
    # Missing `version_pin` + `cache_hit`.
    fake_response.json = MagicMock(return_value={"layered_prompt": "x"})

    mocked_http = MagicMock()
    mocked_http.get = AsyncMock(return_value=fake_response)

    client = _build_client_with_mocked_http(mocked_http=mocked_http)
    with pytest.raises(SoulFileUpstreamError):
        await client.compose(
            influencer_id=_TEST_INFLUENCER_ID,
            user_segment=_TEST_USER_SEGMENT,
        )


# ===========================================================================
# Constructor guard
# ===========================================================================


def test_soul_file_client_constructor_rejects_empty_base_url() -> None:
    """WHAT: SoulFileClient(base_url="") raises ValueError.
    WHEN: half-configured environment.
    WHY:  fail-fast on init rather than crash on first call.
    """
    with pytest.raises(ValueError) as excinfo:
        SoulFileClient(base_url="", call_timeout_seconds=5.0)

    assert "non-empty base_url" in str(excinfo.value)


# ===========================================================================
# RELATED FILES:
#   ../app/soul_file_client.py        — module under test
#   ../app/run_turn.py                — consumer; catches the two typed
#                                        exceptions + maps to 404/503
#                                        envelopes
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                                     — locked orchestrator → soul-file
#                                        contract these tests assert
# ===========================================================================
