# ---------------------------------------------------------------------------
# config.py — typed runtime settings loaded from environment variables.
#
# ⭐ START HERE: this file exposes ONE callable, `get_settings()`, which
# returns a `Settings` instance (cached on first call). Anywhere in the
# codebase that needs a configuration value should do:
#
#     from app.config import get_settings
#     settings = get_settings()
#     dsn = settings.postgres_connection_string
#
# WHY pydantic-settings?
# Three wins over raw `os.environ.get(...)`:
#   1. Typed access — `settings.postgres_connection_string: str` is parsed
#      once, validated, and served as a typed Python value.
#   2. Validation at startup — a missing required field fails fast at first
#      `get_settings()` call, not later inside a request path.
#   3. Single source of truth for env var names — every field name + alias
#      lives here so a typo at a callsite is caught by mypy / IDE.
#
# WHY `validation_alias` FOR THE POSTGRES CONNECTION STRING?
# Per CONSTRAINTS D8: the Swarm secret name must include the service-name
# suffix (`_USER_MEMORY_SERVICE`) so a leaked secret is unambiguous about
# which service's blast-radius applies. But Python code prefers the short
# `postgres_connection_string` identifier. `validation_alias` maps the
# long D8-required env-var name to the short Python field name.
#
# WHY A CACHED SINGLETON (functools.lru_cache)?
# pydantic-settings parses the env exactly once at first construction.
# Subsequent `get_settings()` calls return the same instance without
# re-parsing. Cheap and reduces accidental N-times-per-request env reads.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All env-driven runtime config for the user-memory-service.

    WHAT: typed pydantic-settings model. Each field's default is the
          local-dev / safety-net value; production overrides via env
          vars injected by the Swarm secrets + project.config wiring.
    WHEN: instantiated once via `get_settings()`; the same instance
          serves every callsite for the process lifetime.
    WHY:  central, typed, validated access path replaces scattered
          `os.environ.get(...)` calls across the codebase.
    """

    # `case_sensitive=False` so env var names can be SENTRY_DSN or
    # sentry_dsn — both match. Production env vars are uppercase by
    # convention; locals can be either.
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # -- Service identity ---------------------------------------------------
    # One of: local | staging | production. Read by sentry_middleware,
    # logging, langfuse_middleware to tag events + pick renderers.
    environment: str = "local"

    # Python logging threshold. Read by logging.configure_logging().
    log_level: str = "INFO"

    # -- Sentry (per A7 + D3) ----------------------------------------------
    # Empty in local dev so the SDK no-ops. Populated in prod via the
    # Swarm secret declared in secrets.yaml.
    sentry_dsn: str = ""

    # Tag stamped on every Sentry event (per D3). Sourced from
    # project.config's SENTRY_SERVICE_TAG at deploy time.
    sentry_service_tag: str = "yral-rishi-agent-user-memory-service"

    # Fraction of transactions sampled for performance traces. 0.1 is
    # the conservative starting default.
    sentry_traces_sample_rate: float = 0.1

    # -- Langfuse (per D4) -------------------------------------------------
    # Default OFF — local dev runs without traces. Production deploys
    # flip this to "true" via env injection.
    langfuse_tracing_enabled: bool = False

    # Auth pair (per D8). Empty defaults are the safety net so a
    # half-configured environment still runs (just without traces).
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Host defaults to the production self-hosted Langfuse on rishi-6
    # so a forgotten override still routes to the right place per D4.
    langfuse_host: str = "https://langfuse.rishi.yral.com"

    # -- Postgres (per D8) -------------------------------------------------
    # Per-service connection string read by both the asyncpg runtime pool
    # (`app/database.py`) and the Alembic migration env
    # (`app/migrations/env.py`). The env-var name matches the secret
    # declaration in `secrets.yaml` per D8.
    #
    # Per-service name includes service suffix (`_USER_MEMORY_SERVICE`)
    # per D8 — a leaked credential is unambiguous about its blast radius.
    # asyncpg-compatible: `postgresql://user:pass@host:port/database`.
    postgres_connection_string: str = Field(
        default="",
        validation_alias="POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the module-singleton Settings instance.

    WHAT: constructs `Settings()` once (which triggers env parsing +
          validation), caches the result, returns it on every call.
    WHEN: called from anywhere that needs a config value — usually
          near a function's top, not at module-load (so env vars set
          AFTER import time still take effect in tests).
    WHY:  cached construction means env parsing happens exactly once
          per process, not on every callsite invocation.
    """
    # Construct once — pydantic-settings reads every env var here.
    # Subsequent calls return the cached instance without re-reading.
    return Settings()


# ===========================================================================
# RELATED FILES:
#   main.py                  — calls init_pool() via lifespan (pool reads
#                              postgres_connection_string from settings)
#   database.py              — calls get_settings() for postgres_connection_string
#   sentry_middleware.py     — calls os.environ directly (pre-settings pattern)
#   langfuse_middleware.py   — calls os.environ directly
#   migrations/env.py        — reads POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE
#                              directly from os.environ (same value, separate read)
#   secrets.yaml             — declares POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE
#                              per D8 (blast radius + source per env)
#   pyproject.toml           — declares pydantic-settings dependency
# ===========================================================================
