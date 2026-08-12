"""2026-06-11 — pin the cost-attribution fix.

Before this fix:
- proactive.py + skill-checkin called ai_client.generate_response()
- ai_client picked process=user_chat_main (or _nsfw / _multimodal)
- Result: `llm_costs` rows from proactive were labeled `user_chat_main`
- proactive_generation in LLM_DEFAULTS routed to runpod_vllm primary, but
  that routing was never actually exercised — proactive traffic flowed
  to gemini via the user_chat_main label.

After this fix:
- proactive.py passes `process_override="proactive_generation"`
- ai_client honors the override and skips the NSFW/multimodal heuristic
- llm_costs rows now correctly carry process="proactive_generation"
- LLM_DEFAULTS routing IS honored → runpod_vllm primary → internal_vllm
  fallback, never gemini (per ASYNC_PROCESSES_NEVER_GEMINI leak guard).

These tests pin the contract at the source so a future ai_client refactor
can't silently re-introduce the leak.
"""

import os


def test_proactive_generate_passes_process_override():
    """generate_proactive_message MUST pass process_override on the
    ai_client call so the cost row lands in `proactive_generation` and
    the LLM_DEFAULTS runpod_vllm routing is honored."""
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "services", "proactive.py")
    ).read()
    # First call site (legacy proactive loop, around line 171)
    assert 'process_override="proactive_generation"' in src
    # Both call sites must have it — string occurs at least twice in the
    # file (once per call site to ai_client.generate_response).
    assert src.count('process_override="proactive_generation"') >= 2


def test_ai_client_generate_response_accepts_process_override():
    """ai_client.generate_response must accept the process_override
    parameter — without this, the proactive fix above would TypeError at
    runtime ("got an unexpected keyword argument")."""
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "services", "ai_client.py")
    ).read()
    # Parameter declared on generate_response (not the streaming sibling)
    assert "process_override: str | None = None" in src
    # Must be CONSUMED — bare declaration isn't enough
    assert "if process_override is not None:" in src
    assert "process = process_override" in src


def test_chat_routes_still_omit_process_override():
    """The 3 chat call sites in routes/chat.py MUST keep using the
    default process selection (user_chat_main / _nsfw / _multimodal).
    A regression here would dump chat traffic into proactive_generation
    and break the cost dashboard."""
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "routes", "chat.py")
    ).read()
    # Chat call sites must NOT carry the override kwarg
    assert "process_override" not in src, (
        "routes/chat.py picked up process_override — chat traffic should "
        "stay on the NSFW/multimodal heuristic, not be re-labeled"
    )


def test_proactive_generation_is_in_async_processes_never_gemini():
    """The whole point of the override is to route proactive to
    runpod_vllm. If `proactive_generation` ever drops out of the
    NEVER_GEMINI guard, a future LLM_DEFAULTS bump could silently
    re-route it back to gemini and burn premium $ on background traffic.
    Pin the guard membership."""
    from services.llm_registry import LLM_DEFAULTS

    proactive_cfg = LLM_DEFAULTS["proactive_generation"]
    # Primary must be a non-gemini provider
    assert proactive_cfg["provider"] != "gemini"
    # Fallback must also be non-gemini
    assert proactive_cfg.get("fallback_provider", "gemini") != "gemini"


def test_overrride_skips_nsfw_and_multimodal_heuristic():
    """When process_override is set, the function should use that
    process exactly — NOT mangle it via the is_nsfw/multimodal branches.
    This is the whole point of the override: trust the caller's
    routing intent."""
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "app", "services", "ai_client.py")
    ).read()
    # The override branch must come BEFORE the is_nsfw branch — otherwise
    # NSFW proactive bots would get re-routed to user_chat_main_nsfw,
    # bypassing the override.
    override_pos = src.find("if process_override is not None:")
    nsfw_pos = src.find('process = "user_chat_main_nsfw"')
    assert override_pos > 0
    assert nsfw_pos > 0
    assert override_pos < nsfw_pos, (
        "process_override branch must come BEFORE the NSFW branch — "
        "otherwise NSFW proactive callers get re-labeled to user_chat_main_nsfw"
    )
