"""Phase 25.3 — Gemini native-API client.

Symmetric counterpart to openai_compatible.py — same complete() /
complete_stream() interface, OpenAI messages-list input, LlmResponse
output. Internally translates OpenAI messages → Gemini's
`contents` + `systemInstruction` shape.

Why a separate client instead of running Gemini through
openai_compatible.py: Gemini's native API has materially different
wire format (parts arrays, candidates, finishReason / blockReason
classifications, generationConfig sub-object). Adapting at the client
level is cleaner than at the registry level — registry stays
provider-agnostic.

The legacy `ai_client._call_gemini` keeps its existing surface (Gemini
native `contents` input) until 25.3b finishes the chat-orchestration
migration. Direct callers migrate to llm_registry.call() in this PR.
"""

import logging
import time
from typing import AsyncIterator

import httpx

import config
from services.ai_client import LlmBlockedError, LlmResponse

logger = logging.getLogger(__name__)

GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _messages_to_gemini_contents(
    messages: list[dict],
) -> tuple[list[dict], dict | None]:
    """Convert OpenAI messages → (contents, system_instruction) for Gemini.

    OpenAI shape: [{"role": "system|user|assistant", "content": "..."}]
    Gemini shape:
      contents: [{"role": "user|model", "parts": [{"text": "..."}]}]
      systemInstruction: {"parts": [{"text": "..."}]}  (separate, not in contents)

    Notes:
      - Gemini uses "model" where OpenAI uses "assistant" — rename.
      - System messages get hoisted out of contents into the dedicated
        systemInstruction field. If there are multiple system messages,
        concatenate (rare in practice).
      - Only string content is supported here; multimodal stays in the
        legacy ai_client.generate_response path until 25.3b.
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content") or ""
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": content}]})

    system_instruction = (
        {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    )
    return contents, system_instruction


async def complete(
    *,
    provider: str,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> LlmResponse:
    """Single-shot Gemini completion. Signature mirrors
    openai_compatible.complete() so the registry can dispatch by
    provider with no special-casing in the call() body.

    `base_url` and `extra_body` are accepted for interface symmetry but
    Gemini's native API uses fixed URL + payload shape — base_url is
    ignored, extra_body is merged into generationConfig if present.
    """
    contents, system_instruction = _messages_to_gemini_contents(messages)

    payload: dict = {"contents": contents}
    gen_config: dict = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens
    if gen_config:
        payload["generationConfig"] = gen_config
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    # extra_body is a top-level merge (symmetric to openai_compatible).
    # For Gemini that means callers can pass things like
    # `extra_body={"safetySettings": [...]}` to tune content filters.
    if extra_body:
        payload.update(extra_body)

    url = f"{GEMINI_NATIVE_URL}/models/{model}:generateContent"
    started = time.monotonic()

    # Retry on transient errors (5xx, network). Same shape as
    # openai_compatible — exponential backoff + jitter.
    import asyncio
    import random

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.post(
                    url, json=payload, params={"key": api_key}, timeout=timeout
                )
            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"gemini upstream {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            data = response.json()
            break
        except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                backoff = (0.2 * (2**attempt)) + random.uniform(-0.05, 0.05)
                logger.warning(
                    "gemini.complete retry %d/%d: %s", attempt + 1, max_retries, e
                )
                await asyncio.sleep(max(backoff, 0.05))
                continue
            raise
    else:
        # Loop exhausted without break — re-raise the last error.
        assert last_error is not None
        raise last_error

    candidates = data.get("candidates", [])
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        block_reason = feedback.get("blockReason", "UNKNOWN")
        raise LlmBlockedError(f"blockReason={block_reason}")

    parts = candidates[0].get("content", {}).get("parts", []) or []
    text = "".join(p.get("text", "") for p in parts).strip()

    if not text:
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")
        if finish_reason in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"}:
            raise LlmBlockedError(f"finishReason={finish_reason}")
        raise ValueError(
            f"Gemini returned candidate with no text (finishReason={finish_reason})"
        )

    usage = data.get("usageMetadata", {}) or {}
    return LlmResponse(
        content=text,
        provider=provider,
        model=model,
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        latency_ms=(time.monotonic() - started) * 1000.0,
    )


async def complete_stream(
    *,
    provider: str,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
) -> AsyncIterator[tuple[str, str]]:
    """Streaming Gemini completion. Yields the same ('delta'|'usage'|'done', value)
    contract as openai_compatible.complete_stream so the consumer doesn't
    have to branch on provider.

    Streaming intentionally does NOT retry — consumer is mid-stream.
    """
    import json

    contents, system_instruction = _messages_to_gemini_contents(messages)
    payload: dict = {"contents": contents}
    gen_config: dict = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens
    if gen_config:
        payload["generationConfig"] = gen_config
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if extra_body:
        payload.update(extra_body)

    url = f"{GEMINI_NATIVE_URL}/models/{model}:streamGenerateContent"

    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream(
            "POST", url, json=payload, params={"key": api_key, "alt": "sse"}
        ) as response:
            response.raise_for_status()
            final_usage: dict | None = None
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[len("data: ") :].strip()
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                cands = chunk.get("candidates") or []
                if cands:
                    parts = cands[0].get("content", {}).get("parts", []) or []
                    text = "".join(p.get("text", "") for p in parts)
                    if text:
                        yield ("delta", text)

                usage = chunk.get("usageMetadata")
                if usage:
                    final_usage = usage

            if final_usage:
                yield ("usage", json.dumps(final_usage))
            yield ("done", "")


# ─── Backward-compat shim for legacy callers ─────────────────────────────
#
# config.GEMINI_API_KEY is the env-var fallback. The registry uses
# _resolve_api_key (file-first /run/secrets/<NAME>) — that's the canonical
# path. This helper just lets callers that haven't migrated yet keep
# working without explicitly threading the key through.


def _api_key_or_raise() -> str:
    """Public helper for ai_client.py during the wiring transition.
    Once 25.3b migrates the chat orchestration, this disappears."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("gemini: no GEMINI_API_KEY configured")
    return config.GEMINI_API_KEY
