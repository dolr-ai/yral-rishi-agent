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
from urllib.parse import urlparse

from pydantic import field_validator
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

    # `redis_password`: AUTH credential sent in response to Redis's
    # AUTH challenge. The v2 cluster's Redis primary runs with
    # `--requirepass` enabled (per H3 + 2026-05-22 incident-response
    # rotation), so every connection to the primary MUST AUTH or first
    # command raises
    # `redis.exceptions.AuthenticationError: Authentication required.`
    #
    # Consumed by BOTH Redis paths in this service:
    #   - app/redis_client.py — passed as the `password=` keyword
    #     argument to redis.Redis.from_url() (the JWKS-cache +
    #     idempotency-dedup singleton; uses single-URL connect)
    #   - app/api/health_routes.py — passed as the `password=`
    #     keyword argument to Sentinel.master_for() (the
    #     /health/ready C11 probe)
    #
    # Sourced from the `REDIS_PASSWORD` secret declared in
    # `secrets.yaml` (mounted at `/run/secrets/REDIS_PASSWORD`; env
    # var auto-exported via the compose secret-bridge wrapper).
    # Swarm secret name: `yral_v2_redis_primary_password_<sha>`
    # (versioned via 2026-05-22 rotation pattern; compose maps the
    # logical name → versioned secret via `external: name:`).
    #
    # Empty default keeps local dev working: the docker-compose-
    # bundled Redis is unauthenticated, both code paths skip AUTH
    # when this is empty.
    redis_password: str = ""

    @field_validator("redis_url")
    @classmethod
    def _reject_password_in_redis_url(cls, value: str) -> str:
        """Reject `REDIS_URL` values that embed credentials in the URL.

        WHAT: parse `REDIS_URL` at Settings construction time; raise
              `ValueError` if the URL contains a username or password
              segment (i.e., the `user:pass@` portion before `host`).
        WHEN: every time the Settings model is instantiated — at
              `get_settings()` first call on the lru_cache path, or
              immediately on app boot via the explicit `get_settings()`
              import sites in middleware.
        WHY:  closes Codex PR #137 round-7 BLOCKER 1 — the redis-py
              URL parser takes URL-embedded credentials over the
              `password=` keyword argument, which would silently
              bypass the `REDIS_PASSWORD` Swarm secret rotation
              pattern. Failing LOUDLY at Settings construction is the
              earliest possible diagnosis point; an operator who
              copies the pre-round-8 `redis://:password@host` format
              into a `.env.local` or Swarm secret gets a startup
              crash naming the field instead of a silent runtime
              credential-precedence confusion that would only surface
              when the Swarm secret rotates.
        """
        # Empty URL is technically valid (defaults to redis://localhost
        # if pydantic-settings doesn't apply the field default; defensive
        # short-circuit).
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.username or parsed.password:
            raise ValueError(
                "REDIS_URL must be passwordless — REDIS_PASSWORD is the "
                "sole AUTH source per the round-8 contract. Got a URL "
                "with embedded credentials; strip the `user:pass@` "
                "portion and rely on the REDIS_PASSWORD env var. See "
                "secrets.yaml REDIS_URL description for the full "
                "rationale (Codex PR #137 round-7 BLOCKER 1)."
            )
        return value

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
    # When `enable_strict_jwt_signature_validation` is False (production default),
    # the LEGACY answer is authoritative; strict's result is shadow-
    # logged for divergence analysis. After 7 days with <0.01% divergence
    # rate (per E9 + the JWT shadow-rollout memory), Rishi types YES +
    # this flag flips True; strict becomes authoritative.
    enable_strict_jwt_signature_validation: bool = False

    # URL of the auth.yral.com JWKS document — published list of public
    # keys the strict validator pulls to verify token signatures. Per E6.
    # Production override via env: JWKS_URL=https://auth.yral.com/...
    jwks_url: str = "https://auth.yral.com/.well-known/jwks.json"

    # JWT `iss` claim expected on every token. Strict validator rejects
    # tokens whose `iss` doesn't match this. Default value matches the
    # current chat-ai expected issuer per E6; verify with auth team
    # before flipping `enable_strict_jwt_signature_validation` ON (a wrong default
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

    # How long the JWKS document stays in the Redis cache before
    # re-fetching. **1 hour (3600 s) per E9 verbatim.** Day-3 shipped
    # a per-replica in-process cache at 6h on Rishi's Day-3 directive;
    # Day-4A reconciled to the E9-locked Redis 1hr per coordinator
    # follow-up. Redis-shared cache means ONE JWKS fetch per cluster
    # per hour (not 3 per 6h as the in-process version had); JWKS
    # rotation propagates within 1h cluster-wide; per-
    # operator can override via env JWKS_CACHE_TTL_SECONDS if the
    # JWKS rotation cadence at auth.yral.com changes.
    jwks_cache_ttl_seconds: int = 3600
    # NOTE on `redis_url`: already declared above (PR #97 fixup
    # round-4 forward-ported the setting from this Day-4A commit;
    # Day-4A's original duplicate declaration removed during rebase
    # to keep one source of truth).

    # -- Day-4C orchestrator RPC + idempotency (per
    # interface-contracts/01-internal-rpc-contracts.md + F10) -------------
    # The Session-4 orchestrator base URL public-api forwards every chat
    # turn to. Local dev default routes to the same compose host; in the
    # cluster the Swarm DNS name resolves on the yral-v2-internal overlay.
    # Default matches shared-config.yaml's `services.orchestrator.base_url`.
    orchestrator_base_url: str = (
        "http://yral-rishi-agent-conversation-turn-orchestrator:8000"
    )

    # The path under orchestrator_base_url that handles a single chat
    # turn. Per PR #96 (Session 4 orchestrator handler) + PR #98
    # (coordinator alignment) the canonical path is /v1/turn (NOT /turn
    # as the original contract.md on main showed; PR #98 updates it).
    orchestrator_run_turn_path: str = "/v1/turn"

    # End-to-end timeout for one orchestrator call. 30 s per Day-4C
    # directive — accommodates LLM-bound traffic in Day-5+.
    orchestrator_request_timeout_seconds: float = 30.0

    # Connect-only timeout. 5 s per directive — fails fast on
    # "orchestrator container missing" so the public-api 504 error path
    # differentiates "compute hung" from "service gone."
    orchestrator_connect_timeout_seconds: float = 5.0

    # How long an idempotency-dedup cache entry lives in Redis. 24 hours
    # per F10. Long enough that mobile retries (network drop, app
    # restart, OS push-back-to-foreground) hit the cache; short enough
    # that bounded storage holds across normal traffic patterns.
    idempotency_dedup_ttl_seconds: int = 86400

    # -- Influencer-and-profile-directory RPC (per
    # interface-contracts/01-internal-rpc-contracts.md + DEP-013) ---------
    # The Session-4 influencer-directory base URL public-api proxies
    # /api/v1/influencers reads to. Local dev default routes to the
    # same compose host; in the cluster the Swarm DNS name resolves on
    # the yral-v2-internal overlay (note the `_service` suffix — Swarm's
    # DNS name for a stack service is `<stack>_<service>`). The
    # list-RPC path shape is the DEP-013 proposed contract; the by-id
    # path is the declared contract on main.
    directory_base_url: str = (
        "http://yral-rishi-agent-influencer-and-profile-directory_service:8000"
    )

    # The path under directory_base_url for the list endpoint. DEP-013
    # proposes `/v1/influencers` with `limit` + `offset` query params.
    # Session 4 ratifies when they build the real endpoint.
    directory_list_path: str = "/v1/influencers"

    # The path template under directory_base_url for the by-id endpoint
    # per the contract on main: `GET .../influencers/{id}`. `.format()`
    # is applied with the influencer_id at call site so the path stays
    # configurable without string concatenation in the client.
    directory_by_id_path_template: str = "/v1/influencers/{influencer_id}"

    # End-to-end timeout for one directory call. 5 s — directory is a
    # simple DB-backed lookup with no LLM compute on the path; faster
    # timeout than orchestrator's 30 s so mobile's catalog-fetch
    # doesn't hang on a slow directory + the public-api 503 path
    # surfaces quickly.
    directory_request_timeout_seconds: float = 5.0

    # Connect-only timeout. 2 s — fails fast on "directory container
    # missing" so the public-api 503 error path differentiates "directory
    # gone" from "directory slow." Tighter than orchestrator's 5 s
    # because catalog-fetch is non-LLM-bound.
    directory_connect_timeout_seconds: float = 2.0


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
