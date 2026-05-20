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

    # -- Day-2 placeholder responses gate (per agent definition Day 2) ------
    # Day-2 endpoint handlers return SCHEMA-VALID stubs (not the real
    # responses — those land Day 4 when the orchestrator RPC is wired).
    # This flag MUST stay False in production so a half-built v2 cluster
    # cannot accidentally serve placeholders to real mobile traffic at
    # agent.rishi.yral.com. Local dev + staging flip it to True via env
    # injection so the contract tests + smoke runs pass.
    # When False, every Day-2 chat / influencer handler returns HTTP 503
    # service_unavailable. Flip to True when (a) Day-4 RPC integration
    # is NOT yet in place AND (b) the deploy target is local/staging.
    enable_session_3_phase_1_day_2_placeholder_responses: bool = False

    # -- Redis URL (single-primary fallback path for /health/ready) --------
    # Used by /health/ready's C11-Sentinel fallback path when
    # `redis_sentinel_enabled` is False (laptop dev / docker-compose).
    # Production sets the Sentinel flag to True + lets the Sentinel-
    # aware client discover the current primary at connect time, so
    # this URL is unused in cluster. PR #101's JWKS cache + PR #103's
    # idempotency cache also consume this setting on the Day-4A/4C
    # branches.
    redis_url: str = "redis://localhost:6379/0"

    # -- C11 Sentinel feature flag (Codex PR #97 round-4 BLOCKER 2) --------
    # Default-OFF so laptop dev + docker-compose + CI run on the
    # single-primary `redis_url` fallback above. Production MUST flip
    # to True via env injection (REDIS_SENTINEL_ENABLED=true) so the
    # /health/ready probe (and any future Redis consumer in this
    # service) discovers the current primary via Sentinel quorum +
    # auto-reconnects on failover per C11. When the flag is OFF, the
    # health-route helper emits a LOUD warning
    # `c11_violation_single_primary_redis_no_sentinel` on the fallback
    # path so the C11 gap is visible in startup logs rather than silent.
    # Mirrors Session 4's PR #96 round-3 pattern (commit fe40fcb).
    redis_sentinel_enabled: bool = False

    # -- Day-3 JWT shadow-validation rig (per E9 + agent definition Day 3) --
    # E9 mandates DUAL-validate JWTs during shadow rollout: legacy path
    # (skip signature, accept any well-formed JWT — current chat-ai
    # behavior) runs ALONGSIDE the strict path (full JWKS verification).
    # When `jwt_strict_validation_enabled` is False (production default),
    # the LEGACY answer is authoritative; strict's result is shadow-
    # logged for divergence analysis. After 7 days with <0.01% divergence
    # rate (per E9 + the JWT shadow-rollout memory), Rishi types YES +
    # this flag flips True; strict becomes authoritative.
    # NOTE: PR #101 rebase will rename this to
    # `enable_strict_jwt_signature_validation` (E9-verbatim) — keeping
    # the Day-3 spelling here so this rebase commit is a faithful replay
    # of the original Day-3 work; Day-4A rebase brings the canonical name.
    jwt_strict_validation_enabled: bool = False

    # URL of the auth.yral.com JWKS document — published list of public
    # keys the strict validator pulls to verify token signatures. Per E6.
    # Production override via env: JWKS_URL=https://auth.yral.com/...
    jwks_url: str = "https://auth.yral.com/.well-known/jwks.json"

    # JWT `iss` claim expected on every token. Strict validator rejects
    # tokens whose `iss` doesn't match this. Default value matches the
    # current chat-ai expected issuer per E6; verify with auth team
    # before flipping `jwt_strict_validation_enabled` ON (a wrong default
    # would cause 100% divergence in the shadow log — caught early but
    # noisy).
    jwt_expected_issuer: str = "https://auth.yral.com"

    # JWT `aud` claim expected on every token (the OAuth2 client_id
    # auth.yral.com issued tokens for). Empty default means audience
    # check is SKIPPED in the strict validator (matches chat-ai's
    # current behavior of not enforcing audience). When you know the
    # actual client_id, set this via env to enable the audience check.
    # NOTE: leaving audience empty means strict + legacy agree on the
    # audience dimension; divergence on bad audience is impossible
    # until this is set. Verify with auth team before flipping flag.
    jwt_expected_audience: str = ""

    # How long the JWKS document stays in the per-replica in-process
    # cache before re-fetching. 6 hours (21,600 s) per Rishi's Day-3
    # directive. NOTE: PR #101 (Day-4A) promotes this to Redis 1hr per
    # E9 verbatim. Keeping the Day-3 spelling here so this rebase is a
    # faithful replay; Day-4A rebase brings the E9-correct value.
    jwks_cache_ttl_seconds: int = 21600


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
