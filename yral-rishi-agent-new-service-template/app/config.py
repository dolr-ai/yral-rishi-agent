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
#   2. Validation at startup — a malformed environment variable (e.g.
#      a non-numeric LOG_LEVEL or an empty required field) fails fast
#      at first `get_settings()` call, not later in a request path.
#   3. Single source of truth for environment-variable names — every
#      field name + alias lives here, so a typo at a callsite is
#      caught by mypy / IDE.
#
# WHY NOT shared-config.yaml YET?
# CONSTRAINTS C7 says "shared values live in shared-config.yaml". This PR
# ships environment-variable-only loading because the current consumers
# (sentry, langfuse, logging) need flat scalar values that environment
# variables handle trivially. The YAML loader lands when the first
# consumer needs nested
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

    # `case_sensitive=False` so environment-variable names can be
    # SENTRY_DSN or sentry_dsn — both match the `sentry_dsn` field.
    # Production environment variables are uppercase by convention;
    # locals can be either.
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

    # -- Postgres (per F3 + G3 + D8) ----------------------------------------
    # asyncpg connection string the service's pool reads in `app/database.py`.
    # Template default points at the local docker-compose pgbouncer →
    # postgres pair so `docker compose up --build` works out-of-the-box;
    # production deploys override via the DATABASE_URL Swarm secret per D8.
    database_url: str = (
        "postgresql://service:service-local-password@pgbouncer:6432/service_local_database"
    )

    # Per-service pool bounds. None means "use the module-level constants
    # in `app/database.py`" (min=2, max=20) — keeps the safe baseline.
    # Spawned services with hot-path Postgres dependency override via env
    # vars mapped from project.config's POSTGRES_CONNECTION_LIMIT.
    database_pool_min_size: int | None = None
    database_pool_max_size: int | None = None

    # -- Redis (per C11 + D7) -----------------------------------------------
    # Single-primary fallback URL read by `app/redis_client.py` when
    # `redis_sentinel_enabled=False` (laptop dev / docker-compose / CI).
    # Production deploys flip `redis_sentinel_enabled=True` + the
    # Sentinel-aware path reads master_name + hosts from shared-config.yaml.
    redis_url: str = "redis://redis:6379/0"

    # AUTH credential sent in response to Redis primary's `--requirepass`
    # challenge (per the orchestrator's PR #136 wiring). Empty in local
    # dev (the local redis container has no requirepass); populated in
    # production via the REDIS_PASSWORD Swarm secret. The `or None`
    # guard in `app/redis_client.py` normalises empty-string → None so
    # redis-py skips the AUTH frame entirely on the local path.
    redis_password: str = ""

    # Flag-gated Sentinel-vs-single-primary path selector. False by
    # default so local dev / CI work without Sentinel quorum;
    # production deploys MUST flip to True or `verify_deployed_environment_sentinel_or_die`
    # in `app/redis_client.py` will SystemExit at startup per C11.
    redis_sentinel_enabled: bool = False

    # -- Health probes (per F9 + C7) ----------------------------------------
    # /health/ready dual-probe budget applied to BOTH the asyncpg
    # `SELECT 1` probe in `app/database.py` AND the Redis PING probe
    # in `app/redis_client.py`. Single shared value lives here per C7
    # ("timeouts + thresholds are configurable/shared, not magic
    # constants in code"); Codex PR #151 round-4 BLOCKER moved this
    # out of the per-module `_READINESS_PROBE_TIMEOUT_SECONDS`
    # constants.
    #
    # 200ms default per Codex PR #97 round-4 + Session 4's PR #96
    # round-3 reasoning: health probes MUST fail fast. A blocked
    # probe stalls the asyncio event loop, which stalls every
    # concurrent request, which breaches E1's latency budget on
    # every dep hiccup. 200ms catches "dep is slow" without inviting
    # cascade. Spawned services with E1-tighter budgets can lower
    # this; spawned services with non-hot-path deps can raise it.
    health_ready_probe_timeout_seconds: float = 0.2

    # /health/deep dual-probe budget — applied to BOTH the asyncpg
    # `SELECT NOW()` round-trip in `app/database.py` AND the Redis
    # SET/GET/DEL round-trip in `app/redis_client.py`. Looser budget
    # than /health/ready because deep probes by definition do MORE
    # work (real round-trip + ephemeral state).
    #
    # 1.0s default lets the round-trips complete on a healthy cluster
    # with plenty of slack for jitter, while still catching genuinely
    # unhealthy deps (a healthy Postgres SELECT NOW() returns in
    # single-digit milliseconds; a healthy Redis SET+GET+DEL returns
    # in single-digit milliseconds too). Spawned services that add
    # heavier deep checks (e.g. LLM API connectivity, downstream
    # service ping) can raise this.
    health_deep_probe_timeout_seconds: float = 1.0


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
