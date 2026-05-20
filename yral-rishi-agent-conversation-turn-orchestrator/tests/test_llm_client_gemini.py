# ---------------------------------------------------------------------------
# test_llm_client_gemini.py — Day-5 GeminiClient unit + integration tests.
#
# ⭐ START HERE: this file proves three properties of the Gemini provider:
#   1. The SDK-mocked unit tests verify that the `prompt` + `user_message`
#      both reach the SDK + the LlmResponse fields are populated
#      correctly on the happy path.
#   2. The failure-mode tests prove that timeouts surface as
#      LlmClientTimeoutError + non-timeout SDK errors surface as
#      LlmClientUpstreamError.
#   3. The env-gated integration test (`INTEGRATION_TEST_GEMINI=true`,
#      OFF in CI) hits the real Gemini API + verifies the contract
#      end-to-end. Default-off keeps CI deterministic + cost-free.
#
# WHY MOCK THE SDK (NOT THE HTTP TRANSPORT)
# Per A2.1: the SDK is the boundary that matters for the unit tests.
# Mocking httpx underneath the SDK would couple the test to private
# SDK internals; mocking the SDK's public class is the documented
# boundary.
#
# WHY NO TIMING-BASED TEST OF THE 30s TIMEOUT
# Forcing asyncio.wait_for to time out in <30s would need a custom
# `asyncio.sleep(35)` inside the mock, which makes the test slow
# and flake-prone. Instead the timeout test raises asyncio.TimeoutError
# from the mock directly — exercises the same except branch with
# zero wall-clock dependency.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `asyncio` — used to construct the asyncio.TimeoutError in the
# timeout test + run the async test bodies via pytest-asyncio.
import asyncio

# stdlib `os` — used by the env-gated integration test to read
# `INTEGRATION_TEST_GEMINI` + `GEMINI_API_KEY` at test time.
import os

# `pytest` itself — for the `@pytest.mark.asyncio` decorator + the
# `MonkeyPatch` type for stubbing the SDK module.
import pytest

# `unittest.mock` provides `AsyncMock` + `MagicMock` for the SDK
# substitutions. AsyncMock for the async coroutine method;
# MagicMock for the model wrapper class.
from unittest.mock import AsyncMock, MagicMock

# Imports under test. The two exception shapes are public + tested
# directly. GeminiClient is the concrete provider; LlmResponse is its
# return type.
from app.llm_client import GeminiClient, LlmResponse
from app.llm_client.base import LlmClientTimeoutError, LlmClientUpstreamError


# Helper — a frozen identifier for the mocked SDK module path. The
# Gemini provider does `import google.generativeai as gemini_sdk` so
# every patch target lives under this module path.
_GEMINI_SDK_MODULE: str = "app.llm_client.gemini.gemini_sdk"


# ===========================================================================
# Happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_gemini_client_generate_passes_prompt_and_user_message_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: GeminiClient.generate() passes `prompt` as system_instruction
          + `user_message` as the user turn to the SDK's generate_content.
    WHEN: happy-path chat turn with the SDK mocked.
    WHY:  the contract between run_turn.py and the LLM client is that
          prompt + user_message both reach the upstream API verbatim.
          A regression that drops one of them would silently break
          the soul-file's effect on the assistant's reply.
    """
    # Build a MagicMock SDK module. `configure` is a no-op assertion.
    # `GenerativeModel` is a class; we substitute it with a callable
    # that records its kwargs + returns an instance whose async
    # `generate_content_async` returns a fake SDK response.
    fake_response_object = MagicMock()
    fake_response_object.text = "fake assistant reply"
    fake_response_object.usage_metadata = MagicMock(
        prompt_token_count=42,
        candidates_token_count=17,
    )

    fake_model_instance = MagicMock()
    fake_model_instance.generate_content_async = AsyncMock(
        return_value=fake_response_object,
    )

    fake_model_class = MagicMock(return_value=fake_model_instance)

    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = fake_model_class
    fake_sdk_module.types.GenerationConfig = MagicMock()

    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)

    # Disable Langfuse for this test — get_langfuse returns None per
    # the langfuse_tracing_enabled feature flag default-off.
    monkeypatch.setattr(
        "app.llm_client.gemini.get_langfuse", lambda: None,
    )

    # Act — build client + call generate.
    client = GeminiClient(api_key="fake-key-for-unit-test", model_id="gemini-2.5-flash", call_timeout_seconds=30.0)
    response = await client.generate(
        prompt="soul-file layered prompt under test",
        user_message="hello from the unit test",
        temperature=0.7,
        max_tokens=200,
    )

    # Assert — both inputs reached the SDK + response fields parsed.
    # The per-call `GenerativeModel(...)` constructor in the gemini.py
    # path passes `system_instruction=prompt`. Latest constructor call
    # carries the per-call kwargs.
    per_call_constructor_calls = [
        call_args for call_args in fake_model_class.call_args_list
        if "system_instruction" in (call_args.kwargs or {})
    ]
    assert len(per_call_constructor_calls) == 1, (
        f"expected exactly one per-call GenerativeModel construction with "
        f"system_instruction; got {fake_model_class.call_args_list!r}"
    )
    assert per_call_constructor_calls[0].kwargs["system_instruction"] == (
        "soul-file layered prompt under test"
    )

    # generate_content_async was called once with the user message
    # embedded inside `contents`.
    fake_model_instance.generate_content_async.assert_awaited_once()
    awaited_kwargs = fake_model_instance.generate_content_async.await_args.kwargs
    assert awaited_kwargs["contents"] == [
        {"role": "user", "parts": ["hello from the unit test"]}
    ]

    # LlmResponse fields populated.
    assert isinstance(response, LlmResponse)
    assert response.content == "fake assistant reply"
    assert response.provider == "gemini"
    assert response.model == "gemini-2.5-flash"
    assert response.prompt_tokens == 42
    assert response.completion_tokens == 17
    assert response.latency_milliseconds >= 0


@pytest.mark.asyncio
async def test_gemini_client_generate_returns_zero_token_counts_when_sdk_omits_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: when the SDK response object has no `usage_metadata`,
          the provider returns prompt_tokens=0 + completion_tokens=0
          (defensive defaults) without crashing.
    WHEN: providers that don't report token counts (e.g. streaming-
          only paths in older SDK versions).
    WHY:  LlmResponse must always have all six fields populated; a
          missing metadata attribute MUST NOT crash the chat turn.
    """
    fake_response_object = MagicMock()
    fake_response_object.text = "reply with no usage metadata"
    fake_response_object.usage_metadata = None

    fake_model_instance = MagicMock()
    fake_model_instance.generate_content_async = AsyncMock(
        return_value=fake_response_object,
    )

    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = MagicMock(return_value=fake_model_instance)
    fake_sdk_module.types.GenerationConfig = MagicMock()

    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)
    monkeypatch.setattr(
        "app.llm_client.gemini.get_langfuse", lambda: None,
    )

    client = GeminiClient(api_key="fake-key-for-unit-test", model_id="gemini-2.5-flash", call_timeout_seconds=30.0)
    response = await client.generate(
        prompt="prompt",
        user_message="message",
        temperature=0.5,
        max_tokens=100,
    )

    assert response.prompt_tokens == 0
    assert response.completion_tokens == 0
    assert response.content == "reply with no usage metadata"


# ===========================================================================
# Failure modes
# ===========================================================================


@pytest.mark.asyncio
async def test_gemini_client_generate_raises_timeout_error_on_asyncio_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: when the SDK call exceeds the 30s budget,
          LlmClientTimeoutError is raised + the cause chain preserves
          the original asyncio.TimeoutError.
    WHEN: pathological upstream latency (Day-5+ LLM provider hang).
    WHY:  E1 budget + the directive's 504-envelope contract depend on
          this exception type reaching run_turn.py. A regression that
          swallows TimeoutError would silently degrade tail latency.
    """
    fake_model_instance = MagicMock()
    fake_model_instance.generate_content_async = AsyncMock(
        side_effect=asyncio.TimeoutError(),
    )

    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = MagicMock(return_value=fake_model_instance)
    fake_sdk_module.types.GenerationConfig = MagicMock()

    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)
    monkeypatch.setattr(
        "app.llm_client.gemini.get_langfuse", lambda: None,
    )

    client = GeminiClient(api_key="fake-key-for-unit-test", model_id="gemini-2.5-flash", call_timeout_seconds=30.0)
    with pytest.raises(LlmClientTimeoutError) as excinfo:
        await client.generate(
            prompt="prompt",
            user_message="message",
            temperature=0.7,
            max_tokens=200,
        )

    assert isinstance(excinfo.value.__cause__, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_gemini_client_generate_raises_upstream_error_on_sdk_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: when the SDK raises any non-TimeoutError exception
          (quota, auth, invalid args, 5xx), LlmClientUpstreamError is
          raised + the cause chain preserves the original exception
          type.
    WHEN: upstream API errors (rate-limit, auth-fail, etc.).
    WHY:  502-envelope contract in run_turn.py depends on this
          exception type. A regression that lets the SDK exception
          bubble up unchanged would crash the orchestrator with a
          500 instead of the operator-friendly 502.
    """
    fake_model_instance = MagicMock()
    fake_model_instance.generate_content_async = AsyncMock(
        side_effect=RuntimeError("simulated upstream failure"),
    )

    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = MagicMock(return_value=fake_model_instance)
    fake_sdk_module.types.GenerationConfig = MagicMock()

    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)
    monkeypatch.setattr(
        "app.llm_client.gemini.get_langfuse", lambda: None,
    )

    client = GeminiClient(api_key="fake-key-for-unit-test", model_id="gemini-2.5-flash", call_timeout_seconds=30.0)
    with pytest.raises(LlmClientUpstreamError) as excinfo:
        await client.generate(
            prompt="prompt",
            user_message="message",
            temperature=0.7,
            max_tokens=200,
        )

    assert isinstance(excinfo.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_gemini_client_generate_raises_upstream_error_on_blocked_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: when accessing `sdk_response.text` raises (Gemini's
          safety-filter blocks the candidate response, or the
          candidate is empty / quota-cap'd), the provider maps to
          LlmClientUpstreamError instead of letting the raise escape
          as a generic 500.
    WHEN: a Gemini safety-block / empty-candidate response.
    WHY:  Codex PR-#109 round-2 CONCERN. The Gemini SDK's
          `response.text` property raises ValueError on blocked
          candidates; without the parse-stage try/except in
          `gemini.py`, this lands as the orchestrator's 500 generic-
          exception path. Round-2 fix added the parse-stage try
          mapping to LlmClientUpstreamError so run_turn.py's existing
          502 envelope branch handles it — same operator-side signal
          as quota / auth / 5xx upstream failures.
    """
    # Build an SDK response object whose `.text` property raises
    # ValueError on access — mirrors the real Gemini SDK behaviour
    # for safety-blocked candidates.
    fake_response_object = MagicMock()
    type(fake_response_object).text = property(
        lambda _self: (_ for _ in ()).throw(
            ValueError(
                "Invalid operation: The `response.text` quick accessor "
                "requires the response to contain a valid `Part`, but "
                "none were returned. The candidate's finish_reason is 2."
            )
        )
    )

    fake_model_instance = MagicMock()
    fake_model_instance.generate_content_async = AsyncMock(
        return_value=fake_response_object,
    )

    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = MagicMock(return_value=fake_model_instance)
    fake_sdk_module.types.GenerationConfig = MagicMock()

    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)
    monkeypatch.setattr(
        "app.llm_client.gemini.get_langfuse", lambda: None,
    )

    client = GeminiClient(
        api_key="fake-key-for-unit-test",
        model_id="gemini-2.5-flash",
        call_timeout_seconds=30.0,
    )
    with pytest.raises(LlmClientUpstreamError) as excinfo:
        await client.generate(
            prompt="prompt",
            user_message="trigger safety block",
            temperature=0.7,
            max_tokens=200,
        )

    # The cause chain preserves the original ValueError so operators
    # debugging via Sentry can see exactly what the SDK raised.
    assert isinstance(excinfo.value.__cause__, ValueError)
    # The error message mentions "parse failed" so dashboard filters
    # can distinguish this failure mode from quota / auth / 5xx
    # (which all go through the upstream-error branch but say
    # "Gemini call failed:").
    assert "parse" in str(excinfo.value).lower()


# ===========================================================================
# Constructor guard
# ===========================================================================


def test_gemini_client_constructor_rejects_empty_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: GeminiClient(api_key="") raises ValueError with a clear
          remediation message.
    WHEN: half-configured environment (operator forgot to set
          GEMINI_API_KEY).
    WHY:  fail-fast on init rather than crash on first chat turn.
          The init_default_llm_client lifespan helper relies on this
          to refuse to start the service when the real-LLM flag is
          on but the key is empty.
    """
    fake_sdk_module = MagicMock()
    fake_sdk_module.configure = MagicMock()
    fake_sdk_module.GenerativeModel = MagicMock()
    fake_sdk_module.types.GenerationConfig = MagicMock()
    monkeypatch.setattr(_GEMINI_SDK_MODULE, fake_sdk_module)

    with pytest.raises(ValueError) as excinfo:
        GeminiClient(
            api_key="",
            model_id="gemini-2.5-flash",
            call_timeout_seconds=30.0,
        )

    assert "non-empty api_key" in str(excinfo.value)


# ===========================================================================
# ⭐ ENV-GATED INTEGRATION TEST (real Gemini API; OFF by default)
# ===========================================================================


@pytest.mark.asyncio
async def test_gemini_client_real_api_round_trip_when_env_flag_set() -> None:
    """WHAT: round-trip against the real Gemini API + verify the
          LlmResponse shape end-to-end.
    WHEN: `INTEGRATION_TEST_GEMINI=true` AND `GEMINI_API_KEY` set.
          Skipped otherwise (CI never runs this — it would cost money
          and depend on Google's availability).
    WHY:  catches breakage in the SDK pin / our request shape / the
          upstream API contract. Run manually before each Day-6+ SDK
          version bump.
    """
    if os.environ.get("INTEGRATION_TEST_GEMINI", "false").lower() != "true":
        pytest.skip("INTEGRATION_TEST_GEMINI not set; skipping live Gemini call")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set; cannot run live integration test")

    client = GeminiClient(api_key=api_key, model_id="gemini-2.5-flash", call_timeout_seconds=30.0)
    response = await client.generate(
        prompt=(
            "You are a unit-test helper. Reply with exactly one word: 'pong'. "
            "Do not include any other text, punctuation, or formatting."
        ),
        user_message="ping",
        temperature=0.0,
        max_tokens=10,
    )

    assert isinstance(response, LlmResponse)
    assert isinstance(response.content, str) and len(response.content) > 0
    assert response.provider == "gemini"
    assert response.model == "gemini-2.5-flash"
    assert response.latency_milliseconds > 0


# ===========================================================================
# RELATED FILES:
#   ../app/llm_client/__init__.py   — public package surface (LlmClient,
#                                      LlmResponse, GeminiClient, the two
#                                      exceptions, lifespan helpers)
#   ../app/llm_client/base.py       — abstract LlmClient + LlmResponse +
#                                      exception shapes under test
#   ../app/llm_client/gemini.py     — concrete provider under test
#   ../app/run_turn.py              — consumer; catches the two exceptions
#                                      from LlmClient + maps to envelopes
#   ../app/langfuse_middleware.py   — get_langfuse() singleton stubbed
#                                      to None in every test
# ===========================================================================
