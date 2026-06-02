"""Phase 25.2 SCAFFOLDING ONLY — implementation lands after design approval.

Single source of truth for `process_name → (provider, model, base_url, api_key_secret)`.

See docs/PHASE-25-DESIGN.md for the 3 design decisions:
  1. In-house client over LiteLLM (rationale: ~200 lines we own vs ~40k we don't)
  2. Process names list (every LLM call site gets one stable string)
  3. Per-provider asyncio.Semaphore concurrency cap

Pinned interface (DO NOT change without updating the design doc):

    async def call(
        *,
        process: str,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LlmResponse: ...

    def current_config(process: str) -> dict: ...
        # returns the resolved (provider, model, base_url, api_key_secret)
        # for a given process — used by the admin endpoint + dashboard tile

    async def reload_config_from_db(pool) -> None: ...
        # pulls overrides from `llm_process_config` table on demand;
        # called from `PATCH /admin/llm-registry` (Phase 25.4).

Default static config — see docs/PHASE-25-DESIGN.md "Decision 2" for the full
process-names table. The mapping below is the source-of-truth pin; the
audit lives in the design doc.
"""

# Implementation intentionally omitted until design approval.
# Default registry table will be filled in here once Rishi signs off
# on the 3 decisions in docs/PHASE-25-DESIGN.md.

PROCESS_NAMES: tuple[str, ...] = (
    "user_chat_main",
    "audio_transcription",
    "proactive_generation",
    "quality_scorer",
    "memory_extraction",
    "memory_consolidation",
    "soul_file_coach",
    "nudge_generation",
    "character_generator",
    "ai_influencer_wizard_simulation",
    "soul_file_recommendations",
)
