# ---------------------------------------------------------------------------
# config.py — typed runtime settings loaded from environment variables.
#
# ⭐ START HERE: this file exposes ONE callable, `get_settings()`, which
# returns a `Settings` instance (cached on first call). Anywhere in the
# codebase that needs a configuration value should do:
#
#     from app.config import get_settings
#     settings = get_settings()
#     dsn = settings.sentry_dsn
#
# WHY pydantic-settings?
# Three wins over raw `os.environ.get(...)`:
#   1. Typed access — `settings.sentry_traces_sample_rate: float` instead
#      of parsing strings everywhere.
#   2. Validation at startup — a malformed env var (e.g. a non-numeric
#      LOG_LEVEL or an empty required field) fails fast at first
#      `get_settings()` call, not later in a request path.
#   3. Single source of truth for env var names — every field name + alias
#      lives here, so a typo at a callsite is caught by mypy / IDE.
#
# WHY NOT shared-config.yaml YET?
# CONSTRAINTS C7 says "shared values live in shared-config.yaml". This PR
# ships env-var-only loading because the current consumers (sentry,
# langfuse, logging) need flat scalar values that env vars handle
# trivially. The YAML loader lands when the first consumer needs nested
# structured data (e.g. the Redis Sentinel hosts list per C11, expected
# in a later PR). Adding pyyaml + a yaml-merge processor before any
# consumer needs it would be A2.1 over-engineering.
#
# WHY A CACHED SINGLETON (functools.lru_cache)?
# pydantic-settings parses the env exactly once at first construction.
# Subsequent `get_settings()` calls return the same instance without
# re-parsing. Cheap and reduces accidental N-times-per-request env reads.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All env-driven runtime config for the service.

    WHAT: typed pydantic-settings model. Each field's default is the
          local-dev / safety-net value; production overrides via env
          vars injected by the Swarm secrets + project.config wiring.
    WHEN: instantiated once via `get_settings()`; the same instance
          serves every callsite.
    WHY:  central, typed, validated access path replaces scattered
          `os.environ.get(...)` calls.
    """

    # `case_sensitive=False` so env var names can be SENTRY_DSN or
    # sentry_dsn — both match the `sentry_dsn` field. Production env
    # vars are uppercase by convention; locals can be either.
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # -- Service identity ---------------------------------------------------
    # one of: local | staging | production. Read by sentry_middleware,
    # logging, langfuse_middleware to tag events + pick renderers.
    environment: str = "local"

    # Python logging threshold. Read by logging.configure_logging().
    log_level: str = "INFO"

    # -- Sentry (per A7 + D3) -----------------------------------------------
    # Empty in local dev so the SDK no-ops. Populated in prod via the
    # Swarm secret declared in secrets.yaml.template.
    sentry_dsn: str = ""

    # Tag stamped on every Sentry event (per D3). Sourced from
    # project.config's SENTRY_SERVICE_TAG at deploy time.
    sentry_service_tag: str = "unknown-service"

    # Fraction of transactions sampled for performance traces. 0.1 is
    # the conservative starting default; tune later as the latency
    # gate (per E1) demands.
    sentry_traces_sample_rate: float = 0.1

    # -- Langfuse (per D4) --------------------------------------------------
    # Default OFF — local dev runs without traces. Production deploys
    # flip this to "true" via env injection.
    langfuse_tracing_enabled: bool = False

    # Auth pair (per D8). Empty defaults are the safety net so a
    # half-configured environment runs (just without traces).
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Host defaults to the production self-hosted Langfuse on rishi-6
    # so a forgotten override still routes correctly per D4.
    langfuse_host: str = "https://langfuse.rishi.yral.com"

    # -- Day-2 run_turn stub gate ------------------------------------------
    # When True, the orchestrator's `POST /v1/turn` endpoint returns the
    # Day-2 placeholder MessageDto. False everywhere by default — non-prod
    # environments opt in explicitly via env var
    # `ENABLE_RUN_TURN_STUB=true`, and `app/run_turn.py` ADDITIONALLY
    # refuses to serve the stub when `environment == "production"`
    # regardless of this flag (defence-in-depth so a mis-set env var in
    # prod can never leak a stub reply to mobile parity-test traffic).
    #
    # Why two gates instead of one? Per the Session-4 agent definition's
    # Day-2 plan: "must be schema-valid, must be feature-flagged to
    # non-production, must not leak to mobile parity-test traffic." The
    # environment gate is the unconditional safety net; the flag is the
    # explicit opt-in for the non-prod environments that actually want
    # the stub.
    enable_run_turn_stub: bool = False

    # -- Redis (F10 idempotency + future C11 Sentinel discovery) ---------
    # `redis://` URL read by `app/idempotency.py` to build the async
    # Redis client at lifespan startup. Local default points at the
    # docker-compose sibling container; production swaps in a Sentinel-
    # aware URL per C11. The 24h F10 idempotency TTL is hardcoded in
    # `app/idempotency.py` — no need to expose it as a setting.
    redis_url: str = "redis://localhost:6379/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the module-singleton Settings instance.

    WHAT: constructs `Settings()` once (which triggers env parsing +
          validation), caches the result, returns it on every call.
    WHEN: called from anywhere that needs a config value — usually
          near a function's top, not at module-load (so env vars set
          AFTER import time still take effect).
    WHY:  cached construction means env parsing happens exactly once
          per process, not on every callsite invocation.
    """
    return Settings()


# ===========================================================================
# RELATED FILES:
#   main.py                  — does NOT call get_settings() today; future
#                              consumers (database, redis client, LLM
#                              client) will fetch it instead of reading env
#   sentry_middleware.py     — current env-direct site; can migrate later
#   langfuse_middleware.py   — current env-direct site; can migrate later
#   logging.py               — current env-direct site; can migrate later
#   shared-config.yaml       — nested YAML config (loader added when a
#                              consumer needs structured data per A2.1)
#   pyproject.toml           — declares pydantic-settings dependency
# ===========================================================================
