import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


# App
APP_NAME = _env("APP_NAME", "Yral Agent API")
APP_VERSION = _env("APP_VERSION", "2.0.0")
ENVIRONMENT = _env("ENVIRONMENT", "development")
DEBUG = _env_bool("DEBUG", False)
HOST = _env("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8000)

# Gemini (primary AI model)
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAX_TOKENS = _env_int("GEMINI_MAX_TOKENS", 2048)
GEMINI_TEMPERATURE = _env_float("GEMINI_TEMPERATURE", 0.7)
GEMINI_TIMEOUT = _env_int("GEMINI_TIMEOUT", 60)
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

# Number of prior conversation turns sent as context to the LLM on every
# chat reply. Bumped 10 → 30 (2026-06-04 Rishi) so bots remember within-
# session flow more naturally. Persistent cross-session facts live in
# user_memories (Phase 4) — this window only covers the live exchange.
# Env-overridable for hot-tuning without redeploy.
CHAT_HISTORY_WINDOW = _env_int("CHAT_HISTORY_WINDOW", 30)

# Phase 2.7: SSE streaming for word-by-word AI replies. Mobile decides whether
# to hit the streaming endpoint or the legacy non-streaming one. Backend
# default = TRUE so the streaming path is reachable as soon as mobile is ready.
ENABLE_SSE_STREAMING = _env_bool("ENABLE_SSE_STREAMING", True)

# Coach Bucket 2: when ON, soul_file.compose() prefers a bot's
# `system_instructions_sections` (migration 038) over the flat
# `system_instructions` blob, and Coach proposes against ONE section per
# turn (proposed_section_change shape on coach_messages, migration 039).
# Default OFF so the column is dormant until mobile + backend cutover.
# Per-bot per-env override via COACH_SECTIONED_V2_ENABLED=true. See
# docs/designs/coach-bucket-2-sections-contract.md.
COACH_SECTIONED_V2_ENABLED = _env_bool("COACH_SECTIONED_V2_ENABLED", False)

# OpenRouter (NSFW content routing)
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "google/gemini-2.5-flash")
OPENROUTER_MAX_TOKENS = _env_int("OPENROUTER_MAX_TOKENS", 2048)
OPENROUTER_TEMPERATURE = _env_float("OPENROUTER_TEMPERATURE", 0.7)
OPENROUTER_TIMEOUT = _env_int("OPENROUTER_TIMEOUT", 30)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# S3 storage (Hetzner Object Storage, S3-compatible)
AWS_ACCESS_KEY_ID = _env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _env("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET = _env("AWS_S3_BUCKET")
AWS_REGION = _env("AWS_REGION", "eu-central-1")
S3_ENDPOINT_URL = _env("S3_ENDPOINT_URL")
S3_PUBLIC_URL_BASE = _env("S3_PUBLIC_URL_BASE")
S3_URL_EXPIRES_SECONDS = _env_int("S3_URL_EXPIRES_SECONDS", 900)

# Media limits
MAX_IMAGE_SIZE_MB = _env_int("MAX_IMAGE_SIZE_MB", 10)
MAX_AUDIO_SIZE_MB = _env_int("MAX_AUDIO_SIZE_MB", 20)
MAX_AUDIO_DURATION_SECONDS = _env_int("MAX_AUDIO_DURATION_SECONDS", 300)
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
MAX_AUDIO_SIZE_BYTES = MAX_AUDIO_SIZE_MB * 1024 * 1024

# Replicate (image generation)
REPLICATE_API_TOKEN = _env("REPLICATE_API_TOKEN")
REPLICATE_MODEL = _env("REPLICATE_MODEL", "black-forest-labs/flux-dev")

# Phase 0 Request Images track B — all knobs hot-editable via env override,
# Rishi's ADHD-observability rule (§design). LoRA URL is null in Phase 0
# until Tara's training completes; nano-banana-pro fallback lets the
# pipeline serve immediately.
COLLAGE_IMAGE_COUNT = _env_int("COLLAGE_IMAGE_COUNT", 6)
COLLAGE_THEME_TARA = _env(
    "COLLAGE_THEME_TARA",
    # Suggestive-but-clothed per design §2.5 (in-app collage store
    # rules — explicit belongs on amorae.ai). Filter-safe vocabulary
    # per reference_tara_lora_v1.md: nano-banana-pro refuses
    # lingerie/sheer/boudoir triggers but accepts
    # "high-fashion swimwear" + "editorial" reliably.
    "TAARA on a Santorini clifftop infinity pool at blue hour, "
    "wearing a designer cutout bikini, cinematic sultry pose "
    "with wind-swept hair, editorial Vogue swimwear photography, "
    "dramatic Aegean ocean backdrop, 85mm cinematic lens, "
    "shallow depth of field",
)
COLLAGE_LORA_WEIGHTS_URL = _env("COLLAGE_LORA_WEIGHTS_URL") or None
# Hybrid pipeline (Rishi choice 2026-07-07): when the bot has a LoRA,
# use it as an IDENTITY ANCHOR — generate one anchor image with the
# LoRA per batch, then generate the actual N outputs with
# nano-banana-pro passing the anchor as `image_input`. Rationale:
# nano-banana-pro produces higher-quality scenes, LoRA guarantees
# Tara-identity durability. Anchor per batch (not per theme) so
# today's anchor matches today's theme, giving nano-banana-pro a
# scene-appropriate reference to preserve identity against.
# Set false to fall back to the pure-LoRA path (services/replicate.py
# generate_batch treats it as the pre-hybrid behavior — flux-dev +
# LoRA for all N).
COLLAGE_HYBRID_MODE = _env("COLLAGE_HYBRID_MODE", "true").lower() == "true"
# Estimated marginal cost per generation. Real cost lives in the
# provider bill; this is what we book into influencer_collages.cost_usd
# for the daily-budget guard. Hybrid = 1 flux-dev-lora anchor (~$0.03)
# + N nano-banana-pro (~$0.04 each), so amortized per-image is ~$0.045
# for a 6-image batch. Pure LoRA is ~$0.03/img; pure nano is ~$0.04.
COLLAGE_COST_PER_IMAGE_USD = _env_float("COLLAGE_COST_PER_IMAGE_USD", 0.045)
COLLAGE_DAILY_BUDGET_SOFT_USD = _env_float("COLLAGE_DAILY_BUDGET_SOFT_USD", 50.0)
COLLAGE_DAILY_BUDGET_HARD_USD = _env_float("COLLAGE_DAILY_BUDGET_HARD_USD", 100.0)
COLLAGE_POLL_TIMEOUT_SEC = _env_int("COLLAGE_POLL_TIMEOUT_SEC", 90)
COLLAGE_POLL_INTERVAL_SEC = _env_int("COLLAGE_POLL_INTERVAL_SEC", 2)
# Comma-separated YRAL-team principals who get clear (unblurred)
# collages during Phase 0. Real billing.yral.com integration is Phase 1.
YRAL_TEAM_PRINCIPALS = frozenset(
    p.strip() for p in _env("YRAL_TEAM_PRINCIPALS", "").split(",") if p.strip()
)

# Push notifications
METADATA_URL = _env("METADATA_URL", "https://metadata.yral.com")
METADATA_AUTH_TOKEN = _env("YRAL_METADATA_NOTIFICATION_API_KEY")
NAITIK_MULTI_SERVICE_URL = _env("NAITIK_MULTI_SERVICE_URL", "https://multi-service.naitik.yral.com")
NAITIK_MULTI_SERVICE_AUTH_TOKEN = _env("NAITIK_MULTI_SERVICE_AUTH_TOKEN")

# CORS
CORS_ORIGINS = _env("CORS_ORIGINS", "*")

# Rate limiting
RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 300)
RATE_LIMIT_PER_HOUR = _env_int("RATE_LIMIT_PER_HOUR", 5000)

# How many recent messages get images inlined when calling Gemini
IMAGE_HISTORY_WINDOW = _env_int("IMAGE_HISTORY_WINDOW", 3)

# Admin
ADMIN_KEY = _env("ADMIN_KEY_TO_DELETE_INFLUENCER")

# Google Chat webhook (admin notifications)
GOOGLE_CHAT_WEBHOOK_URL = _env("GOOGLE_CHAT_WEBHOOK_URL")

# Billing
BILLING_URL = _env("BILLING_URL", "https://billing.yral.com")

# Langfuse (LLM observability)
LANGFUSE_SECRET_KEY = _env("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = _env("LANGFUSE_PUBLIC_KEY")
LANGFUSE_HOST = _env("LANGFUSE_HOST")

# JWT auth
EXPECTED_ISSUERS = ["https://auth.yral.com", "https://auth.dolr.ai"]
