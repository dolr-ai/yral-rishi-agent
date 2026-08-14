"""Client-side rate limiter + 429 Retry-After handling (2026-08-14).

Hetzner's free tier returns 429 + Retry-After: 5 once we exceed its rate. These
pin that a per-provider token bucket paces requests and that call() honours
Retry-After before giving up. Async cases run via asyncio.run (no pytest-asyncio
in this repo); no live LLM calls.
"""

import asyncio

import httpx
import pytest

from services import llm_registry
from services.llm_registry import _RateLimiter, _rate_limiter, _retry_after_seconds


# ─── provider config ────────────────────────────────────────────────────


def test_hetzner_has_rate_limit_others_dont():
    assert llm_registry.PROVIDERS["hetzner"].get("rate_limit_per_min")
    assert _rate_limiter("hetzner") is not None
    # Providers without a rate_limit_per_min get no client-side pacing.
    assert _rate_limiter("gemini") is None
    assert _rate_limiter("runpod_vllm") is None


# ─── token bucket ───────────────────────────────────────────────────────


def test_token_bucket_bursts_then_blocks():
    async def run():
        # per_min=60 → 1 token/s, burst capacity max(1, 60/12)=5.
        rl = _RateLimiter(per_min=60)
        granted = 0
        for _ in range(20):
            if await rl.acquire(max_wait=0.0):
                granted += 1
            else:
                break
        assert 1 <= granted <= 6, f"burst should be ~capacity, got {granted}"
        # Bucket drained → a zero-wait acquire fails fast (no blocking).
        assert await rl.acquire(max_wait=0.0) is False

    asyncio.run(run())


def test_token_bucket_grants_after_short_wait():
    async def run():
        rl = _RateLimiter(per_min=6000)  # 100/s → refills quickly
        while await rl.acquire(max_wait=0.0):
            pass  # drain the burst
        # A small wait budget lets a token refill and be granted.
        assert await rl.acquire(max_wait=1.0) is True

    asyncio.run(run())


# ─── Retry-After extraction ─────────────────────────────────────────────


def _http_429(retry_after=None):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    req = httpx.Request("POST", "https://inference.hetzner.com/api/v1/chat/completions")
    resp = httpx.Response(429, headers=headers, request=req)
    return httpx.HTTPStatusError("429", request=req, response=resp)


def test_retry_after_from_header():
    assert _retry_after_seconds(_http_429("5")) == 5.0


def test_retry_after_defaults_when_no_header():
    assert _retry_after_seconds(_http_429(None)) == llm_registry._DEFAULT_RETRY_AFTER_SEC


def test_retry_after_capped():
    assert _retry_after_seconds(_http_429("999")) == llm_registry._MAX_RETRY_AFTER_SEC


def test_retry_after_none_for_non_429():
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(500, request=req)
    err = httpx.HTTPStatusError("500", request=req, response=resp)
    assert _retry_after_seconds(err) is None
    assert _retry_after_seconds(RuntimeError("boom")) is None


# ─── call() 429 retry behaviour ─────────────────────────────────────────

_NO_FALLBACK_CFG = {"provider": "hetzner", "model": "m", "timeout_sec": 60.0}


def test_call_retries_on_429_then_succeeds(monkeypatch):
    from services.llm_types import LlmResponse

    attempts = {"n": 0}

    async def fake_do_complete(*, provider, model, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _http_429("0")  # retry_after 0 → no real sleep
        return LlmResponse(
            content="ok", provider=provider, model=model,
            input_tokens=1, output_tokens=1, latency_ms=1.0,
        )

    monkeypatch.setattr(llm_registry, "_do_complete", fake_do_complete)
    monkeypatch.setattr(llm_registry, "_process_config", lambda _p: dict(_NO_FALLBACK_CFG))
    r = asyncio.run(
        llm_registry.call(process="quality_scorer", messages=[{"role": "user", "content": "x"}])
    )
    assert r.content == "ok"
    assert attempts["n"] == 2  # first 429, retry succeeded


def test_call_429_exhausts_retries_then_raises_without_fallback(monkeypatch):
    attempts = {"n": 0}

    async def always_429(*, provider, model, **kwargs):
        attempts["n"] += 1
        raise _http_429("0")

    monkeypatch.setattr(llm_registry, "_do_complete", always_429)
    monkeypatch.setattr(llm_registry, "_process_config", lambda _p: dict(_NO_FALLBACK_CFG))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            llm_registry.call(process="quality_scorer", messages=[{"role": "user", "content": "x"}])
        )
    # initial attempt + _MAX_RATE_RETRIES retries
    assert attempts["n"] == llm_registry._MAX_RATE_RETRIES + 1
