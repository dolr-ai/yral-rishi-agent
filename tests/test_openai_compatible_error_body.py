"""Regression test for the OpenRouter `'choices'` KeyError.

Sentry issue YRAL-RISHI-AGENT-4J (2026-06-18) caught the production
class of bug: OpenRouter returns 2xx with `{"error": {...}}` body
on rate-limit (and a few safety-block paths). The body lacks
`choices`. The old `data["choices"][0]` access raised
`KeyError: 'choices'` which surfaced to mobile as a `TRANSIENT`
fallback with no hint of the real reason — 7 users affected over
2 days before triage caught it.

Fix: defensive parse in `openai_compatible.complete` — if `choices`
absent, re-raise as `httpx.HTTPStatusError` carrying the upstream
`error.message`. The existing retry ladder + ai_client mapper see
the actual cause + retry+log accordingly.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]

# Behavioural tests need httpx installed (i.e. a CI env with
# requirements.txt). Locally on a bare interpreter we skip them —
# the source-pin test stays alive to guard the diff shape.
try:
    import httpx  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

requires_httpx = pytest.mark.skipif(
    not _HTTPX_AVAILABLE,
    reason="httpx not installed (local dev); covered by CI",
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_defensive_choices_guard_present():
    """A future PR can't accidentally restore the bare `data["choices"][0]`
    that crashed in prod. Pin the guard."""
    src = (
        REPO / "app" / "services" / "llm_clients" / "openai_compatible.py"
    ).read_text()
    assert '"choices" not in data' in src
    # The fix re-raises as HTTPStatusError so the existing retry ladder
    # treats it like a 5xx / network error (uniform handling).
    assert "raise httpx.HTTPStatusError" in src


# ─── behavioural — stubbed httpx ────────────────────────────────────────


if _HTTPX_AVAILABLE:
    import httpx


class _StubResponse:
    """Just enough surface for the complete() retry path."""

    def __init__(
        self,
        status_code: int,
        json_body: dict,
        *,
        url: str = "https://openrouter.ai/api/v1/chat/completions",
    ):
        self.status_code = status_code
        self._json = json_body
        self.request = httpx.Request("POST", url)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"upstream {self.status_code}",
                request=self.request,
                response=self,
            )


class _StubClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        self.calls += 1
        if not self._responses:
            raise AssertionError("stub exhausted")
        return self._responses.pop(0)


def _patch_httpx(monkeypatch, stub):
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: stub, raising=True)


@requires_httpx
def test_complete_raises_when_body_has_error_no_choices(monkeypatch):
    """The 2026-06-18 prod failure: OpenRouter returns 200 with
    `{"error": {"message": "Rate limit exceeded", "code":
    "rate_limit_exceeded"}}` and the bare `data["choices"][0]` blows
    up with a generic KeyError. The fix re-raises as HTTPStatusError
    with the upstream message embedded."""
    from services.llm_clients.openai_compatible import complete

    err_body = {
        "error": {
            "message": "Rate limit exceeded for requests per minute",
            "code": "rate_limit_exceeded",
        }
    }
    # Provide 3 stub responses so the retry ladder exhausts them all
    # (max_retries=3 by default in complete()).
    stub = _StubClient([_StubResponse(200, err_body) for _ in range(3)])
    _patch_httpx(monkeypatch, stub)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(
            complete(
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key="test",
                model="google/gemini-2.5-flash",
                messages=[{"role": "user", "content": "hi"}],
                max_retries=3,
            )
        )
    err_text = str(exc_info.value)
    # The upstream error.message must propagate so logs (+ Sentry)
    # show the real cause instead of `KeyError: 'choices'`.
    assert "Rate limit exceeded" in err_text
    assert "rate_limit_exceeded" in err_text
    # All 3 retries attempted (provider error treated uniformly with 5xx).
    assert stub.calls == 3


@requires_httpx
def test_complete_raises_when_body_has_empty_choices(monkeypatch):
    """Edge case: `{"choices": []}` (no error key either). Defensive
    `not data.get("choices")` catches the empty list too — without
    the guard, `choices[0]` would raise IndexError."""
    from services.llm_clients.openai_compatible import complete

    stub = _StubClient([_StubResponse(200, {"choices": []}) for _ in range(2)])
    _patch_httpx(monkeypatch, stub)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(
            complete(
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key="test",
                model="google/gemini-2.5-flash",
                messages=[{"role": "user", "content": "hi"}],
                max_retries=2,
            )
        )
    err_text = str(exc_info.value)
    assert "no choices" in err_text


@requires_httpx
def test_complete_happy_path_still_returns_content(monkeypatch):
    """Defensive guard must NOT regress the success path. Standard
    OpenAI-spec body with one choice returns the content as before."""
    from services.llm_clients.openai_compatible import complete

    ok_body = {
        "choices": [{"message": {"content": "hello back"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    stub = _StubClient([_StubResponse(200, ok_body)])
    _patch_httpx(monkeypatch, stub)

    result = asyncio.run(
        complete(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test",
            model="google/gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert result.content == "hello back"
    assert result.input_tokens == 5
    assert result.output_tokens == 2
    assert result.provider == "openrouter"
