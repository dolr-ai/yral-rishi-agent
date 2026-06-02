"""Shared LLM types — extracted from ai_client.py to break the
ai_client → registry → gemini → ai_client circular import that surfaces
when generate_response calls llm_registry.call (Phase 25.3b).

Single home for the response shape + error classes + canonical fallback
text. Both ai_client.py (chat orchestrator) and the per-provider clients
(gemini.py, openai_compatible.py) import from here.
"""

from dataclasses import dataclass


# Phase 3.8: tailored fallback text by failure class. Mobile reads
# LlmResponse.error_code to pick icon/color/retry button.
ERROR_MESSAGES = {
    "BLOCKED_CONTENT": "I can't reply to that — try asking me something else.",
    "TRANSIENT": "I'm having trouble connecting right now. Try again in a moment.",
    "NO_PROVIDER": "Chat is temporarily unavailable. Please try again later.",
}
RETRYABLE_CODES = {"TRANSIENT"}


class LlmBlockedError(Exception):
    """Gemini/OpenRouter refused to generate due to safety/policy."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LlmResponse:
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    is_fallback: bool = False
    error_code: str | None = None
