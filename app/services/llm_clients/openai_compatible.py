"""Phase 25.1 SCAFFOLDING ONLY — implementation lands after design approval.

Generic client for any OpenAI-spec `/v1/chat/completions` endpoint:
OpenAI, OpenRouter, Together, vLLM, Saikat self-hosted, Ollama.

See docs/PHASE-25-DESIGN.md for the LiteLLM-vs-in-house decision and
the per-provider concurrency cap design.

Pinned interface (DO NOT change without updating the design doc):

    async def complete(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        timeout: float = 60.0,
    ) -> LlmResponse: ...

    async def complete_stream(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[tuple[str, str]]: ...
        # yields (kind, value) where kind in {"delta", "done", "error"}

`extra_body` is merged into the JSON request body alongside the standard
fields. Required for internal_vllm (Qwen 3.6 thinking-mode disable):
    extra_body={"chat_template_kwargs": {"enable_thinking": False}}
Standard providers (OpenAI, OpenRouter) silently ignore unknown keys, so
the same code path works for all targets — no per-provider branching.

`LlmResponse` is the existing dataclass from `app/services/ai_client.py`
— reused, not redefined, so the response shape stays symmetric across
both clients (Gemini native + OpenAI-compatible).
"""

# Implementation intentionally omitted until design approval.
# Importing this module today is safe — it has no side effects.
