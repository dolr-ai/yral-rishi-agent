"""Phase 25.1 — OpenAI-spec /v1/chat/completions client.

One client that talks to any provider exposing the OpenAI Chat
Completions wire format: OpenAI, OpenRouter, Together, internal_vllm
(Anshuman's Qwen 3.6), Ollama.

See docs/PHASE-25-DESIGN.md for the LiteLLM-vs-in-house rationale and
the per-provider concurrency cap design. Cost recording happens in the
caller (Phase 25.5); this client only returns LlmResponse with token
counts populated.
"""

import asyncio
import json
import logging
import random
import time
from typing import AsyncIterator

import httpx

from services.llm_types import LlmResponse

logger = logging.getLogger(__name__)


async def complete(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> LlmResponse:
    """Single-shot completion. Retries with exponential backoff on
    transient errors (5xx, network) up to `max_retries` times.

    `provider` is the registry key (gemini / openai / internal_vllm).
    It's stamped onto LlmResponse for downstream cost recording; the
    client itself is provider-agnostic.

    `extra_body` is merged into the JSON request body — required for
    internal_vllm's chat_template_kwargs (Qwen 3.6 thinking-mode off).
    Standard providers silently ignore unknown keys."""
    body: dict = {"model": model, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if extra_body:
        body.update(extra_body)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=body)
            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"upstream {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            data = response.json()
            # Sentry issue YRAL-RISHI-AGENT-4J (2026-06-18): OpenRouter
            # returns 2xx with `{"error":{"message":"...","code":"..."}}`
            # on rate-limit + some safety-block paths — body shape lacks
            # `choices`. Previously the bare `data["choices"][0]` raised
            # `KeyError: 'choices'` which leaked to mobile as a TRANSIENT
            # fallback with no hint of the real reason. Surface the
            # provider's `error.message` through the existing httpx
            # raise path so the retry loop + the ai_client error mapper
            # see the actual cause.
            if "choices" not in data or not data.get("choices"):
                err = data.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                detail = (
                    msg
                    or f"upstream returned no choices (body keys: {list(data.keys())[:5]})"
                )
                # Re-raise as HTTPStatusError so the existing retry
                # ladder treats provider errors uniformly with 5xx /
                # network errors. Synthesize a 502 to signal "upstream
                # gave us a malformed response."
                raise httpx.HTTPStatusError(
                    f"{provider} returned error body (code={code}): {detail}",
                    request=response.request,
                    response=response,
                )
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
            usage = data.get("usage") or {}
            return LlmResponse(
                content=content,
                provider=provider,
                model=model,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # 200ms → 400ms → 800ms ± 50ms jitter
                backoff = (0.2 * (2**attempt)) + random.uniform(-0.05, 0.05)
                logger.warning(
                    "openai_compatible.complete retry %d/%d on %s: %s",
                    attempt + 1,
                    max_retries,
                    provider,
                    e,
                )
                await asyncio.sleep(max(backoff, 0.05))
                continue
            break

    assert last_error is not None
    raise last_error


async def complete_stream(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
) -> AsyncIterator[tuple[str, str]]:
    """Streaming completion. Yields (kind, value) tuples where:
      ('delta', token_str)  — content delta from the model
      ('usage', json_str)   — final chunk with prompt/completion tokens
      ('done',  '')         — stream complete

    Per Anshuman's gist (see design doc), `stream_options.include_usage`
    causes the LAST chunk to carry usage with empty `choices`. We surface
    it as a discrete event so the caller doesn't have to parse SSE again.
    Streaming intentionally does NOT retry — the consumer is mid-stream;
    retry decisions belong to the orchestrator."""
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if extra_body:
        body.update(extra_body)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :].strip()
                if payload == "[DONE]":
                    yield ("done", "")
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(
                        "openai_compatible.complete_stream: bad JSON chunk on %s: %s",
                        provider,
                        payload[:200],
                    )
                    continue

                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield ("delta", content)

                usage = chunk.get("usage")
                if usage:
                    yield ("usage", json.dumps(usage))
            yield ("done", "")
