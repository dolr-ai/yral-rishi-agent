"""Phase 21αβ.H12 — vision-bearing chat routes via user_chat_main_multimodal.

The 2026-06-08 bug: Rishi flipped user_chat_main → runpod_vllm
(text-only Qwen pod) via the admin dashboard; chat messages with image
attachments silently failed because the pod has no vision support.

This split mirrors the audio_transcription pattern:
- Dedicated process the admin guard protects.
- Capability flag (supports_vision) on every provider.
- Routing detector that picks the process based on payload content.

Mixed tests:
- Static (source-pin) for the admin route capability guard (mirrors the
  audio_transcription guard test pattern in test_llm_routing_admin.py).
- Behavioral for PROVIDERS / LLM_DEFAULTS / has_image_content (these
  are pure data + pure function, no I/O).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_supports_vision_flag_set_on_all_providers():
    """Every provider in PROVIDERS must declare supports_vision. Missing
    the flag would mean the capability guard silently lets a flip through
    (default-False is checked by .get(), but the omission is a code smell
    — explicit is better than implicit)."""
    from services.llm_registry import PROVIDERS

    expected = {
        "gemini": True,
        "openai": True,
        "openrouter": True,
        "runpod_vllm": False,
        "internal_vllm": False,
        "ollama": False,
    }
    for provider, want in expected.items():
        assert provider in PROVIDERS, f"provider {provider} missing from PROVIDERS"
        meta = PROVIDERS[provider]
        assert "supports_vision" in meta, (
            f"provider {provider} missing supports_vision flag — every provider "
            f"must declare it explicitly so the admin guard never sees a default"
        )
        assert meta["supports_vision"] is want, (
            f"provider {provider} supports_vision={meta['supports_vision']!r} "
            f"but expected {want!r}"
        )


def test_user_chat_main_multimodal_in_defaults_with_no_fallback():
    """The new process must default to gemini and have NO fallback.
    A text-only fallback would silently drop images at the exact moment
    vision matters; failing loud is the right behavior."""
    from services.llm_registry import LLM_DEFAULTS, PROCESS_NAMES

    assert "user_chat_main_multimodal" in PROCESS_NAMES
    assert "user_chat_main_multimodal" in LLM_DEFAULTS
    cfg = LLM_DEFAULTS["user_chat_main_multimodal"]
    assert cfg["provider"] == "gemini"
    assert cfg["model"] == "gemini-2.5-flash"
    assert cfg["timeout_sec"] == 60.0
    # NO fallback — explicit absence (failing loud > silent-text-only fallback).
    assert "fallback_provider" not in cfg
    assert "fallback_model" not in cfg


def test_admin_endpoint_capability_check_vision():
    """Mirror of the audio_transcription capability guard test. Refusing
    to point user_chat_main_multimodal at a non-vision provider is what
    makes the supports_vision flag load-bearing. Without this gate, the
    2026-06-08 bug would just recur via the admin dashboard."""
    src = (REPO / "app" / "routes" / "llm_routing_admin.py").read_text()
    assert "supports_vision" in src
    assert "user_chat_main_multimodal" in src
    # Both the PATCH endpoint and the form-submit endpoint must check.
    # We count occurrences of the guard expression — should be at least 2.
    assert src.count("user_chat_main_multimodal") >= 2, (
        "vision guard must be wired into BOTH PATCH and form endpoints"
    )


def test_admin_endpoint_accepts_openrouter_for_multimodal():
    """openrouter has supports_vision=True (Gemini-2.5-flash via OR is
    multimodal). The guard must NOT reject it. Source-pin: the guard
    text rejects only on `not supports_vision` — so any provider with
    the flag set to True passes."""
    from services.llm_registry import PROVIDERS

    assert PROVIDERS["openrouter"]["supports_vision"] is True


def test_has_image_content_detects_openai_style_image_url():
    """OpenAI-style image_url part — what _build_user_content emits."""
    from services.llm_registry import has_image_content

    messages = [
        {"role": "system", "content": "You are an AI."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,abc"},
                },
            ],
        },
    ]
    assert has_image_content(messages) is True


def test_has_image_content_detects_responses_api_input_image():
    """Responses API uses input_image instead of image_url. Detector
    must handle both — the message-building code could switch between
    them depending on provider SDK without retriggering this routing
    decision."""
    from services.llm_registry import has_image_content

    messages = [
        {"role": "user", "content": [{"type": "input_image", "image_url": "x"}]},
    ]
    assert has_image_content(messages) is True


def test_has_image_content_detects_gemini_native_inline_data():
    """Gemini-native shape (no `type` key; nested `inlineData`).
    Recognizing this keeps the detector robust as the builder evolves."""
    from services.llm_registry import has_image_content

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": "base64-bytes",
                    }
                }
            ],
        },
    ]
    assert has_image_content(messages) is True


def test_has_image_content_text_only_is_false():
    """Pure text messages must NOT trigger multimodal routing. This is
    the common case and must stay fast (text routing path = unchanged)."""
    from services.llm_registry import has_image_content

    assert has_image_content([]) is False
    assert has_image_content([{"role": "user", "content": "hello"}]) is False
    assert (
        has_image_content(
            [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        )
        is False
    )


def test_user_chat_main_unchanged_for_text_only_regression_guard():
    """Regression: user_chat_main's default config must NOT change as a
    side-effect of adding the multimodal process. Text-only chat keeps
    going through user_chat_main exactly as before; the H12 split only
    diverts vision-bearing requests."""
    from services.llm_registry import LLM_DEFAULTS

    cfg = LLM_DEFAULTS["user_chat_main"]
    # Gemini default per the morning's routing policy (sync user-waiting).
    assert cfg["provider"] == "gemini"
    assert cfg["model"] == "gemini-2.5-flash"


def test_ai_client_routes_multimodal_on_image_payload():
    """Source-pin the routing call in ai_client.py. Both the non-stream
    and stream paths must consult has_image_content before picking a
    process. Without this, the multimodal process would be reachable
    only via direct admin override — defeating the H12 split."""
    src = (REPO / "app" / "services" / "ai_client.py").read_text()
    assert "has_image_content" in src
    assert "user_chat_main_multimodal" in src
    # Both call sites: generate_response + generate_response_stream
    assert src.count("user_chat_main_multimodal") >= 2


def test_multimodal_process_not_in_async_never_gemini():
    """user_chat_main_multimodal is user-facing and stays on gemini
    today (the only multimodal-capable provider in our default lineup).
    It must NOT be in ASYNC_PROCESSES_NEVER_GEMINI — otherwise the leak
    guard would fire on every multimodal chat."""
    from services.llm_registry import ASYNC_PROCESSES_NEVER_GEMINI

    assert "user_chat_main_multimodal" not in ASYNC_PROCESSES_NEVER_GEMINI
