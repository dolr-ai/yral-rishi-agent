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

# JWT auth
EXPECTED_ISSUERS = ["https://auth.yral.com", "https://auth.dolr.ai"]
