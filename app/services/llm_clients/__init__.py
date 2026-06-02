"""Phase 25 — per-provider LLM client implementations.

Each module here is one client class implementing the same interface
(`async def complete(model, messages, **kwargs) -> LlmResponse`). The
provider registry in `app/services/llm_registry.py` dispatches each
process to one of these clients based on config.

See docs/PHASE-25-DESIGN.md for the design + open questions.
"""
