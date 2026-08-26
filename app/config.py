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


def _env_list(key: str, default: str = "") -> list[str]:
    """Comma-separated env var → list of trimmed, non-empty strings.

    Empty string yields `[]` rather than `[""]`, so an unset feature flag
    is falsy and reads naturally as "no entries" at the call site — the
    property MARKET_EXCLUSIVE_COUNTRIES depends on to ship dormant."""
    return [item.strip() for item in _env(key, default).split(",") if item.strip()]


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

# Profile-picture storage (Storj — dedicated bucket + public link-share).
# Reuses the SAME Storj gateway creds + endpoint as the chat-media store above
# (AWS_ACCESS_KEY_ID/SECRET, S3_ENDPOINT_URL) — only the bucket differs — so no
# new credentials are needed. Kept separate from chat media because a profile-pic
# URL is written to SpacetimeDB and rendered by clients directly, forever, so it
# must be durable + public. Storj has no public-read policy; public access is via
# the link-sharing service, so PROFILE_PIC_PUBLIC_URL_BASE is the bucket's
# read-only, non-expiring `/raw/` link-share and the full URL is base + "/" + key.
PROFILE_PIC_S3_BUCKET = _env("PROFILE_PIC_S3_BUCKET", "yral-profile-pictures")
PROFILE_PIC_PUBLIC_URL_BASE = _env(
    "PROFILE_PIC_PUBLIC_URL_BASE",
    "https://link.storjshare.io/raw/juojrspbmsy7dtukovdepimmpnma/yral-profile-pictures",
)

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
# Downstream model for the hybrid pipeline (LoRA anchor → N × downstream).
# Default flipped 2026-07-15 from `google/nano-banana-pro` to
# `black-forest-labs/flux-kontext-dev` after Google tightened the nano
# safety filter mid-week and every Tara batch started landing state=failed
# (see COLLAGE_FALLBACK_MAX_DAYS comment below for the incident).
# flux-kontext-dev is Replicate-native, has no Google filter dependency,
# and accepts a single `input_image` reference — identical anchor
# semantics, different transport. Hot-editable env var so we can flip
# back to nano-banana-pro if the filter loosens or we want its scene
# quality on non-NSFW bots (where the filter doesn't bite).
COLLAGE_HYBRID_DOWNSTREAM_MODEL = _env(
    "COLLAGE_HYBRID_DOWNSTREAM_MODEL",
    "black-forest-labs/flux-kontext-dev",
)
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
# 2026-07-13 — endpoint hardening after Replicate/Google tightened the
# nano-banana-pro safety filter mid-week and every Tara pre-gen row
# landed as state='failed'. When today's row is failed OR the
# elected-generator race is lost to a failed peer, orchestrate() falls
# back to the most-recent succeeded row for the same bot within this
# window instead of bubbling 502. 0 disables the fallback (revert to
# pre-2026-07-13 behavior) — the paranoid switch.
COLLAGE_FALLBACK_MAX_DAYS = _env_int("COLLAGE_FALLBACK_MAX_DAYS", 7)
# Gaussian blur radius applied to non-subscriber variants. 15 px is
# the "teasy but face-unreadable" sweet spot Rishi picked after
# eyeballing 10/15/20/30 radii on 2026-07-08. Hot-tunable so we
# don't need a code push to shift the paywall aesthetic.
COLLAGE_BLUR_RADIUS_PX = _env_int("COLLAGE_BLUR_RADIUS_PX", 15)
# Comma-separated YRAL-team principals who get clear (unblurred)
# collages during Phase 0. Real billing.yral.com integration is Phase 1.
YRAL_TEAM_PRINCIPALS = frozenset(
    p.strip() for p in _env("YRAL_TEAM_PRINCIPALS", "").split(",") if p.strip()
)

# Push notifications
METADATA_URL = _env("METADATA_URL", "https://metadata.yral.com")
METADATA_AUTH_TOKEN = _env("YRAL_METADATA_NOTIFICATION_API_KEY")

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
EXPECTED_ISSUERS = ["https://auth.yral.com"]

# yral-auth v2 issues ES256-signed JWTs; the public key is published at the JWKS
# endpoint (fetched + cached, keyed by `kid` so rotation is automatic). Tokens
# are always signature-verified — there is no unverified fallback.
JWKS_URL = _env("JWKS_URL", "https://auth.yral.com/.well-known/jwks.json")

# Market targeting (US market launch — see
# docs/us-market-launch-spec-2026-08-08.md)
#
# Countries whose users see ONLY personas tagged for that market on the
# discovery surfaces. Empty (the default) means today's behaviour
# everywhere, which is how this ships dormant: PR1 adds the column and
# these knobs, PR2 adds the filter, and nothing changes for anyone until
# this list is non-empty. Hot-editable so the US feed can be switched on
# and off with an env change rather than a deploy — and reversed the same
# way if it misbehaves.
MARKET_EXCLUSIVE_COUNTRIES = _env_list("MARKET_EXCLUSIVE_COUNTRIES", "")

# Lets QA drive the market with an X-Market-Debug header so the whole US
# feed is verifiable by curl, with no mobile build and no app review.
# That header is trivially spoofable, so this MUST stay false in prod —
# otherwise any user could pick their own catalogue.
MARKET_DEBUG_OVERRIDE_ENABLED = _env_bool("MARKET_DEBUG_OVERRIDE_ENABLED", False)

# ─── Video generation ──────────────────────────────────────────────────
#
# On by default. The only knob is the kill switch ENABLE_VIDEOGEN_LOOP (see
# kill_switch.py) — deliberately not a second flag here, so there is one place
# to look when asking "is video generation on?", and one thing to flip to stop
# it without a redeploy.
#
# ComfyUI on the GPU box (rishi-gpu-1). It listens on 127.0.0.1:18188 there and
# has NO authentication of its own, so it must never be exposed openly — reach
# it over a tunnel and keep the shared token set.
COMFYUI_BASE_URL = _env("COMFYUI_BASE_URL", "http://127.0.0.1:18188")
COMFYUI_AUTH_TOKEN = _env("COMFYUI_AUTH_TOKEN", "")
COMFYUI_TIMEOUT_SECONDS = _env_int("COMFYUI_TIMEOUT_SECONDS", 120)
# Connecting is a hop across the local overlay and takes milliseconds when it
# works at all, so it gets its own short budget. Sharing the 120s read timeout
# meant one unreachable tunnel task held a user's request for two minutes
# before failing (2026-08-26).
COMFYUI_CONNECT_TIMEOUT_SECONDS = _env_int("COMFYUI_CONNECT_TIMEOUT_SECONDS", 5)
# The tunnel is a Swarm global service behind one virtual IP; a retry opens a
# new connection and so lands on a different task. See videogen/comfyui.py.
COMFYUI_ATTEMPTS = _env_int("COMFYUI_ATTEMPTS", 3)

# Finished videos — a dedicated Storj bucket, reusing the same gateway
# credentials as chat media and profile pictures (only the bucket differs).
# Storj rather than the Hetzner bucket behind today's CDN for the reason
# profile pictures chose it: the Hetzner credentials also reach the DB-backup
# bucket, and a public-facing upload path should not hold those.
#
# THE KEY LAYOUT IS THE CONTRACT, not this URL. The app never reads a video URL
# from us — it builds one itself from the post's video_uid and creator:
#     https://cdn-yral-sfw.yral.com/{principal}/{video_id}.mp4
# So a generated video only plays once that CDN hostname serves this bucket.
# Until it does, generation works end-to-end and playback 404s. That is a
# Cloudflare origin change, not a code change — see app/videogen/README.md.
VIDEOGEN_S3_BUCKET = _env("VIDEOGEN_S3_BUCKET", "yral-videos")
VIDEOGEN_PUBLIC_URL_BASE = _env(
    "VIDEOGEN_PUBLIC_URL_BASE", "https://cdn-yral-sfw.yral.com"
)

# Poll cadence and the giving-up point. Generation runs a couple of minutes;
# the stale window only has to be generous enough that a queued job behind
# other work is not retired while it is still legitimately waiting.
VIDEOGEN_POLL_INTERVAL_SECONDS = _env_int("VIDEOGEN_POLL_INTERVAL_SECONDS", 15)
VIDEOGEN_STALE_AFTER_SECONDS = _env_int("VIDEOGEN_STALE_AFTER_SECONDS", 1800)

# How long one loop copy owns a claimed row. Must comfortably exceed a full
# generation (~2-3 min observed) or a healthy job is picked up twice; must stay
# well under VIDEOGEN_STALE_AFTER_SECONDS or a dead worker's row is retired
# before anyone re-claims it.
VIDEOGEN_CLAIM_LEASE_SECONDS = _env_int("VIDEOGEN_CLAIM_LEASE_SECONDS", 600)

# SpacetimeDB holds the posts. We call reducers as the requesting user by
# forwarding their token, so there is no admin credential here to leak.
SPACETIMEDB_URL = _env("SPACETIMEDB_URL", "https://maincloud.spacetimedb.com")
SPACETIMEDB_DB_NAME = _env("SPACETIMEDB_DB_NAME", "yral-database-spacetime-4lbo7")
SPACETIMEDB_TIMEOUT_SECONDS = _env_int("SPACETIMEDB_TIMEOUT_SECONDS", 30)
