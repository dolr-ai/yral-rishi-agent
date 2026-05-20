# ---------------------------------------------------------------------------
# idempotency.py — F10 default-on idempotency for POST /v1/turn, C11-aware.
#
# ⭐ START HERE: this module exposes the lifecycle pair + the dedup
# decision API that the run_turn handler dispatches on per request:
#
#   Lifecycle:
#     - `init_redis()` / `close_redis()`     — FastAPI lifespan wires these
#     - `get_redis()`                        — accessor for tests + helpers
#
#   Dedup decision (call ONCE at handler entry):
#     - `acquire_or_check(key, fingerprint)` → IdempotencyDecision
#         The handler dispatches on `.state`:
#           "acquired"            → proceed, build response, then mark_complete
#           "replay_done"         → return `.cached_response` byte-for-byte
#           "fingerprint_mismatch"→ 409 envelope (same key, different body)
#           "in_flight_timeout"   → 503 envelope (lock held but never completed)
#
#   Handler success path (call ONCE after acquire="acquired" + work):
#     - `mark_complete(key, fingerprint, response_payload)`
#
#   Key construction:
#     - `compute_idempotency_key(user_id, idempotency_key)`
#     - `compute_request_fingerprint(body_dict)`
#
# WHY F10 — DEFAULT-ON IDEMPOTENCY ON EVERY NON-GET ENDPOINT
# Per CONSTRAINTS F10 verbatim: "Idempotency-key default-on on all
# non-GET endpoints; dedupes via Redis 24hr TTL. Per-endpoint opt-out
# for truly stateless." `POST /v1/turn` is the orchestrator's single
# non-GET endpoint today; it MUST honour F10 from day 1.
#
# WHY ATOMIC LOCK (SET NX) — Codex PR-#96 round-3 BLOCKER 1b
# The previous round-2 fix used GET-then-SET, which is RACE-PRONE:
# two concurrent POSTs with the same key could BOTH miss the cache,
# BOTH execute the handler, BOTH write the response (second SET
# silently overwriting the first). F10 + the round-3 contract update
# at PR #98 commit 31d1dac require atomic dedup against concurrent
# duplicate requests. We use `SET key value NX EX 86400` as the
# in-progress lock primitive — the FIRST caller to acquire proceeds;
# concurrent duplicates poll the key until the first caller marks
# completion (or fingerprint-mismatch returns 409 immediately).
#
# WHY FINGERPRINT — Codex round-3 BLOCKER 1b
# Stores `sha256(canonical_json(body))` alongside the cached response
# so the same idempotency key reused with a DIFFERENT body cannot
# replay the wrong reply. The orchestrator rejects with 409 instead
# of returning a stale match. Same fingerprint = byte-identical
# replay (the F10 happy path).
#
# WHY C11 SENTINEL — Codex round-3 BLOCKER 2
# Per CONSTRAINTS C11 verbatim: "Redis HA via Sentinel (not Cluster).
# Primary on rishi-4, replica on rishi-5, Sentinel quorum on
# rishi-4/5/6. All Python services use `redis.sentinel.Sentinel`
# client to discover current primary." The previous round-2 fix used
# `redis.asyncio.Redis.from_url(...)` directly — that breaks when
# Sentinel fails over the primary. This round wires the Sentinel-
# aware client, reading hosts + master name from `shared-config.yaml`
# (per C7). Behind a feature flag (`redis_sentinel_enabled`,
# default-OFF) so laptop dev keeps working with the docker-compose
# single-primary Redis; a startup WARNING fires when the flag is OFF
# so the C11 gap stays loud, not silent.
#
# WHY redis.asyncio (NOT redis-py sync)
# Per F12 the runtime stack is asyncio-native. Sync redis-py inside
# an async handler would block the event loop on every cache check.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib async sleep — used by the poll-on-lock loop between Redis
# GET attempts when another concurrent request holds the lock.
import asyncio

# stdlib SHA-256 — used by `compute_request_fingerprint` to hash the
# canonical-JSON of the request body. SHA-256 is plenty for the
# collision-resistance we need here (catching same-key-different-body).
import hashlib

# stdlib JSON serialiser — used to canonicalise the body for fingerprint
# hashing AND to encode/decode the in-progress lock + done states
# stored in Redis as JSON strings.
import json

# stdlib logger — emits structured fields the H6 PII-allowlist redactor
# in `app/logging.py` knows about (idempotency hit / lock state /
# Sentinel enablement). We log key METADATA, never the cached payload
# or the user message content.
import logging

# stdlib path helpers — used to locate `shared-config.yaml` at the
# service folder root from this module's location (`app/idempotency.py`).
import pathlib

# `dataclass` + `field` give us a typed return object for
# `acquire_or_check` so the handler dispatches on `.state` instead of
# unpacking a multi-value tuple. `Final` marks the module constants
# as immutable. `Literal` constrains `.state` to the four valid
# string values the handler dispatches on.
from dataclasses import dataclass
from typing import Final, Literal

# `redis.asyncio` is the async Redis client. `Sentinel` is the
# Sentinel-aware version that discovers the current primary at
# connect time + reconnects on failover.
import redis.asyncio as redis_asyncio
from redis.asyncio.sentinel import Sentinel

# PyYAML — used to read `shared-config.yaml` once at `init_redis()`
# time so the Sentinel master name + hosts come from C7's single
# source-of-truth, not from env vars duplicated per service.
import yaml

# `get_settings()` reads the typed Settings singleton; we need the
# `redis_url` fallback + the `redis_sentinel_enabled` feature flag
# declared in `app/config.py`.
from app.config import get_settings


# Module-level Redis singleton — populated by `init_redis()` at app
# startup and consumed via `get_redis()`. `None` before init / after
# close so any out-of-lifecycle access fails fast.
_redis: redis_asyncio.Redis | None = None


# 24 hours in seconds — the F10 dedup window. Locked per F10 verbatim
# ("dedupes via Redis 24hr TTL"). The Redis-side TTL is just the
# storage mechanism that backs this app-semantic dedup window;
# renamed from `_IDEMPOTENCY_TTL_SECONDS` per Codex PR-#96 round-4
# BLOCKER 4 (B2 disallows the `TTL` abbreviation in our identifier
# names — the application-level concept is "dedup window", the
# Redis-side concept is its `EX` parameter). Changing the duration
# value requires a CONSTRAINTS amendment.
_IDEMPOTENCY_DEDUP_WINDOW_SECONDS: Final[int] = 24 * 60 * 60


# Key prefix shape — `idempotency:orchestrator:run-turn:{user_id}:{key}`.
_KEY_PREFIX: Final[str] = "idempotency:orchestrator:run-turn"


# Poll-on-lock retry parameters. 50ms × 20 attempts = 1s ceiling per
# the Codex round-3 BLOCKER 1b directive. Tuned for a stub handler
# latency budget (<<1s); Day-5+ real LLM calls may bump the ceiling
# to match the upstream LLM provider's p99 latency.
_POLL_INTERVAL_SECONDS: Final[float] = 0.05
_POLL_MAX_ATTEMPTS: Final[int] = 20


# State enum for the in-Redis JSON payload. Two terminal values; the
# `state` key inside the JSON tells the poll loop whether to keep
# waiting or harvest the completed response.
_STATE_IN_PROGRESS: Final[str] = "in_progress"
_STATE_DONE: Final[str] = "done"


_log = logging.getLogger("app.idempotency")


# ===========================================================================
# H6-safe log helpers
# ===========================================================================


def _idempotency_key_hash_prefix(redis_key: str) -> str:
    """Return the first 16 hex chars of sha256(redis_key) — the H6-safe log id.

    WHAT: sha256-hashes the fully-qualified Redis key, returns the first
          16 hex chars.
    WHEN: called from every log site that previously emitted the raw
          last segment of the Redis key (the idempotency key value).
    WHY:  Codex PR-#96 round-4 BLOCKER 3 — a malicious or buggy client
          can pass arbitrary text in the X-Idempotency-Key header before
          the UUID-format validation lands. Even POST-validation, the
          raw key bytes shouldn't reach Sentry / Langfuse / structured
          logs by default (H6 PII-allowlist defence-in-depth). The
          16-char hash prefix is enough entropy for grep-correlation
          across services without leaking the original value.
    """
    return hashlib.sha256(redis_key.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# Decision type
# ===========================================================================


@dataclass(frozen=True)
class IdempotencyDecision:
    """Outcome of the per-request dedup lookup.

    WHAT: a typed result object the run_turn handler dispatches on. One
          field (`state`) covers the four mutually-exclusive outcomes;
          `cached_response` is populated only on `replay_done`.
    WHEN: returned by `acquire_or_check(...)` once per request before
          the handler builds + returns its MessageResponse.
    WHY:  beats a 4-tuple return value or 4 raises-or-returns — a
          single dataclass means callsite dispatch is `match decision.state`
          and adding a fifth state is one new Literal value, not a new
          throw site.
    """

    # Which of the four flows the handler should run:
    #   "acquired"            → no concurrent dup; proceed + mark_complete
    #   "replay_done"         → cached response in .cached_response; return it
    #   "fingerprint_mismatch"→ same key, different body → 409 envelope
    #   "in_flight_timeout"   → lock held but never completed → 503 envelope
    state: Literal[
        "acquired",
        "replay_done",
        "fingerprint_mismatch",
        "in_flight_timeout",
    ]

    # Populated ONLY when `state == "replay_done"`. The handler turns
    # this back into a `MessageResponse` via `MessageResponse(**...)`.
    cached_response: dict | None = None


# ===========================================================================
# Lifecycle
# ===========================================================================


def _load_redis_section_from_shared_config() -> dict:
    """Read the `redis:` section of `shared-config.yaml` at the service root.

    WHAT: opens `shared-config.yaml` (two directories up from this
          module's __file__), parses it, returns `data["redis"]`.
    WHEN: invoked once at `init_redis()` time when the Sentinel-aware
          path is enabled.
    WHY:  C7 says "shared values live in shared-config.yaml" — the
          Sentinel master name + the 3 sentinel host:port pairs come
          from there, not from env vars duplicated across services.
    """
    config_path = (
        pathlib.Path(__file__).resolve().parent.parent / "shared-config.yaml"
    )
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    redis_section = data.get("redis", {})
    if not isinstance(redis_section, dict):
        raise RuntimeError(
            "shared-config.yaml `redis:` section is not a mapping; "
            "check the file syntax."
        )
    return redis_section


async def init_redis() -> None:
    """Open the async Redis connection (Sentinel-aware or single-primary).

    WHAT: builds either a Sentinel-aware client (Sentinel discovers
          current primary at connect time + reconnects on failover)
          or a single-primary client from `redis_url`. Stores it in
          the module-level `_redis` variable.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  central init means every callsite sees the same pooled
          client + we can teardown cleanly via `close_redis()` on
          SIGTERM. The flag-gated fallback keeps laptop dev working
          while the cluster's Sentinel hostnames stabilise.

    Raises:
        SystemExit when `environment="production"` AND
        `redis_sentinel_enabled=False` — C11 fail-closed gate (Codex
        PR-#96 round-4 BLOCKER 1). Production MUST run on Sentinel;
        the previous round-3 single-primary fallback's WARNING was
        not enforcement. Laptop dev (`environment="local"`) keeps
        the fallback path.
        RuntimeError when sentinel-enabled but the shared-config.yaml
        `redis:` section is missing the master name or hosts.
    """
    global _redis

    if _redis is not None:
        # Already initialised — idempotent no-op (helpful for tests
        # that pre-inject a fakeredis instance via monkeypatch).
        # Tests that explicitly want to exercise the production-fail-
        # closed gate set `_redis = None` BEFORE calling init_redis;
        # production deploys start with `_redis = None` by construction.
        _log.debug("init_redis called but already initialised; skipping")
        return

    settings = get_settings()

    # -----------------------------------------------------------------
    # BLOCKER 1 (round-4) — fail-closed in production.
    # -----------------------------------------------------------------
    # The previous round-3 fix logged a WARNING on the single-primary
    # fallback path. Codex correctly flagged that a warning is not
    # enforcement — a production deploy with the wrong env var lands
    # silently. This block raises SystemExit instead so the process
    # refuses to start, and the operator-facing message names the
    # exact env var to set + the alternative remediation.
    #
    # Check runs AFTER the `_redis is not None` short-circuit so the
    # auto-use `fake_redis` fixture in tests (which pre-injects a
    # FakeRedis instance) bypasses this gate cleanly. The
    # production-fail-closed regression test explicitly sets
    # `_redis = None` before calling `_REAL_INIT_REDIS_FOR_TESTS()`
    # so the gate fires the way it would on a fresh production
    # process startup.
    if (
        settings.environment == "production"
        and not settings.redis_sentinel_enabled
    ):
        critical_message = (
            "C11 violation: production environment requires Redis "
            "Sentinel; set REDIS_SENTINEL_ENABLED=true OR fix "
            "shared-config.yaml's `redis.sentinel_master_name` + "
            "`redis.sentinel_hosts` populated by Session 1's cluster "
            "bootstrap. Refusing to start."
        )
        _log.critical(
            "c11_violation_production_requires_sentinel",
            extra={
                "environment": settings.environment,
                "redis_sentinel_enabled": settings.redis_sentinel_enabled,
            },
        )
        raise SystemExit(critical_message)

    if settings.redis_sentinel_enabled:
        # C11-compliant Sentinel path.
        redis_section = _load_redis_section_from_shared_config()
        master_name = redis_section.get("sentinel_master_name", "")
        raw_hosts = redis_section.get("sentinel_hosts", [])

        if not master_name or not raw_hosts:
            raise RuntimeError(
                "redis_sentinel_enabled=True but shared-config.yaml's "
                "`redis.sentinel_master_name` or `redis.sentinel_hosts` "
                "is empty. Populate from the cluster bootstrap before "
                "flipping the flag on."
            )

        # Sentinel expects a list of (host, port) tuples. The YAML
        # stores each entry as `{host: ..., port: ...}` for readability.
        sentinel_targets = [
            (entry["host"], int(entry["port"]))
            for entry in raw_hosts
        ]

        sentinel_client = Sentinel(
            sentinel_targets,
            socket_timeout=0.5,
            decode_responses=True,
        )

        # `master_for` returns a Redis client that re-resolves the
        # current primary on every command (no stale-primary bug after
        # failover).
        _redis = sentinel_client.master_for(
            master_name,
            decode_responses=True,
        )
        _log.info(
            "redis_client_initialised_via_sentinel",
            extra={
                "master_name": master_name,
                "sentinel_count": len(sentinel_targets),
            },
        )
        return

    # Single-primary fallback path (laptop dev / docker-compose).
    url = settings.redis_url
    if not url:
        raise RuntimeError(
            "redis_url is empty AND redis_sentinel_enabled is False — "
            "set REDIS_URL or flip redis_sentinel_enabled=True."
        )

    _redis = redis_asyncio.Redis.from_url(url, decode_responses=True)

    # LOUD warning so the C11 gap is visible in startup logs whenever
    # this fallback path is taken. CI / prod must run with the
    # Sentinel flag ON; this warning is the operator-side signal.
    _log.warning(
        "c11_violation_single_primary_redis_no_sentinel",
        extra={
            "url_scheme": url.split("://", 1)[0],
            "remediation": (
                "set redis_sentinel_enabled=True + ensure shared-config.yaml "
                "redis.sentinel_master_name + sentinel_hosts are populated "
                "from the cluster bootstrap"
            ),
        },
    )


async def close_redis() -> None:
    """Close the Redis client cleanly.

    WHAT: awaits `_redis.aclose()` to flush pending commands + tear
          down the connection pool, then sets `_redis = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM.
    WHY:  uncleaned Redis connections persist on the server side until
          their idle timeout; clean shutdown == faster Swarm rolls.
    """
    global _redis

    if _redis is None:
        return

    await _redis.aclose()
    _redis = None
    _log.info("redis_client_closed")


def get_redis() -> redis_asyncio.Redis:
    """Return the initialised async Redis client.

    WHAT: returns the module-level `_redis`. Raises if init hasn't run.
    WHEN: called from `acquire_or_check` + `mark_complete` (any code
          path doing a Redis read or write).
    WHY:  central accessor — a future refactor that swaps the client
          implementation (e.g. ACL-aware per-service Redis) only
          touches `init_redis` + `get_redis`.
    """
    if _redis is None:
        raise RuntimeError(
            "redis client is not initialised — call `init_redis()` in the "
            "FastAPI lifespan startup hook before any request handler."
        )
    return _redis


# ===========================================================================
# Key + fingerprint construction
# ===========================================================================


def compute_idempotency_key(user_id: str, idempotency_key: str) -> str:
    """Return the fully-qualified Redis key for this user+key pair.

    WHAT: formats `idempotency:orchestrator:run-turn:{user_id}:{key}`.
    WHEN: called once per request by the run_turn handler.
    WHY:  one mapping point — if the key shape needs to change (e.g.
          add a region prefix), this is the only file to edit.
    """
    return f"{_KEY_PREFIX}:{user_id}:{idempotency_key}"


def compute_request_fingerprint(body_payload: dict) -> str:
    """Return a SHA-256 hex digest of the canonical-JSON request body.

    WHAT: serialises `body_payload` to canonical JSON
          (sort_keys=True + compact separators) and hashes via SHA-256.
          The hex digest goes into the Redis lock payload so a
          different body with the same idempotency key produces a
          different fingerprint and triggers the 409 path.
    WHEN: called once per request by the run_turn handler before the
          dedup decision.
    WHY:  canonicalising key order means two equivalent JSON dicts
          (which Python serialises with arbitrary key order) produce
          the SAME fingerprint. Without that, the test for byte-equal
          replay between two POSTs would flake.
    """
    canonical_json = json.dumps(
        body_payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ===========================================================================
# Dedup decision — SET NX in-progress lock + poll-on-held + fingerprint check
# ===========================================================================


async def acquire_or_check(
    redis_key: str,
    fingerprint: str,
) -> IdempotencyDecision:
    """Atomic-acquire the in-progress lock or harvest the prior result.

    WHAT: attempts `SET redis_key <in-progress-payload> NX EX 86400`.
          On acquire → returns `acquired`. On lock-held → polls the
          key with bounded retry until the value transitions to
          `done` (returns `replay_done` + cached payload), the
          fingerprint mismatches (returns `fingerprint_mismatch`),
          or the poll ceiling is hit (returns `in_flight_timeout`).
    WHEN: called once at the top of every run_turn handler invocation
          (after the gates fire + after the X-Idempotency-Key header
          required check).
    WHY:  atomic-by-design dedup against concurrent duplicates — F10
          + Codex round-3 BLOCKER 1b. The single `SET NX` is the only
          critical section; everything after it is observation, not
          a second write.

    Returns:
        IdempotencyDecision — see the dataclass docstring for the four
        possible `state` values + when `cached_response` is populated.
    """
    redis_client = get_redis()

    in_progress_payload = json.dumps(
        {
            "state": _STATE_IN_PROGRESS,
            "fingerprint": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    # The critical section — atomic SET-NX. Returns True (or non-None
    # in newer redis-py) on acquire; None / False when the key already
    # exists. We treat any truthy result as acquired.
    acquired = await redis_client.set(
        redis_key,
        in_progress_payload,
        nx=True,
        ex=_IDEMPOTENCY_DEDUP_WINDOW_SECONDS,
    )

    if acquired:
        _log.info(
            "idempotency_lock_acquired",
            extra={"idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key)},
        )
        return IdempotencyDecision(state="acquired")

    # Lock held by someone else — poll the key until it transitions
    # to done (or fingerprint mismatches, or we hit the ceiling).
    _log.info(
        "idempotency_lock_held_polling",
        extra={"idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key)},
    )

    for attempt_index in range(_POLL_MAX_ATTEMPTS):
        current_value_text = await redis_client.get(redis_key)

        # Lock might have expired between SET-NX and GET (very rare —
        # 24h TTL); keep polling to give the next-write request time
        # to land. The poll ceiling still applies.
        if current_value_text is not None:
            try:
                parsed_lock_payload = json.loads(current_value_text)
            except json.JSONDecodeError:
                # Corrupt entry — treat as miss + log so we can alert
                # if it happens in volume.
                _log.warning(
                    "idempotency_lock_corrupt",
                    extra={"idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key)},
                )
                parsed_lock_payload = None

            if parsed_lock_payload is not None:
                stored_fingerprint = parsed_lock_payload.get("fingerprint")

                # Fingerprint mismatch — same idempotency key reused
                # with a different body. Reject 409 immediately; don't
                # wait for the holder to complete (the cached payload
                # would be the wrong reply for THIS request).
                if stored_fingerprint != fingerprint:
                    _log.warning(
                        "idempotency_fingerprint_mismatch",
                        extra={
                            "idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key),
                        },
                    )
                    return IdempotencyDecision(state="fingerprint_mismatch")

                # Fingerprint matches → check state.
                if parsed_lock_payload.get("state") == _STATE_DONE:
                    cached_response = parsed_lock_payload.get("response")
                    _log.info(
                        "idempotency_replay_done",
                        extra={
                            "idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key),
                        },
                    )
                    return IdempotencyDecision(
                        state="replay_done",
                        cached_response=cached_response,
                    )

                # state == "in_progress" + matching fingerprint —
                # another request for the same body is still in
                # flight. Sleep + retry GET.

        # Sleep before next poll. Don't sleep on the LAST attempt
        # (saves one sleep before the timeout return) — but the cost
        # is negligible and skipping makes the loop body branchier.
        # Per A2.1 keep the loop body simple.
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    _log.warning(
        "idempotency_in_flight_timeout",
        extra={
            "idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key),
            "polled_attempts": _POLL_MAX_ATTEMPTS,
        },
    )
    return IdempotencyDecision(state="in_flight_timeout")


async def mark_complete(
    redis_key: str,
    fingerprint: str,
    response_payload: dict,
) -> None:
    """Overwrite the in-progress lock with the completed response.

    WHAT: SETs the key to `{state:done, fingerprint, response}` with a
          fresh 24h TTL. Overwrites the in-progress payload the
          acquiring caller wrote earlier.
    WHEN: called from the run_turn handler AFTER the happy-path
          MessageResponse build, before returning to the caller.
    WHY:  transitions the in-progress lock to the done-state every
          concurrent waiter will harvest via the poll loop in
          `acquire_or_check`.
    """
    redis_client = get_redis()

    done_payload = json.dumps(
        {
            "state": _STATE_DONE,
            "fingerprint": fingerprint,
            "response": response_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    # No `nx=True` here — we WANT to overwrite the in-progress lock we
    # set earlier. Fresh `ex=` so the done-state gets the full 24h
    # window (otherwise a slow handler would leave very little TTL for
    # the replay window).
    await redis_client.set(
        redis_key,
        done_payload,
        ex=_IDEMPOTENCY_DEDUP_WINDOW_SECONDS,
    )

    _log.info(
        "idempotency_marked_complete",
        extra={"idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key)},
    )


async def release_in_progress_lock(redis_key: str) -> None:
    """Delete the in-progress lock so a retry can acquire fresh.

    WHAT: issues `DELETE redis_key` against Redis. Safe to call when the
          key may or may not exist (Redis DELETE is a no-op for missing
          keys + returns the count of removed keys).
    WHEN: called from the run_turn handler's exception path AFTER
          `acquire_or_check` returned `state="acquired"` but BEFORE
          `mark_complete` ran (i.e. the handler raised mid-execution).
          NEVER called on the happy path — `mark_complete` already
          transitioned the lock to `done`, which is the cached payload
          every concurrent waiter + every subsequent retry expects to
          serve from the F10 dedup window.
    WHY:  Codex PR-#96 round-5 finding: the previous round-4 code had no
          failure-path cleanup. If the handler raised between
          `acquire_or_check` returning `acquired` and `mark_complete`
          running, the in-progress lock stayed in Redis for the full
          24-hour dedup window. A buggy chat turn (or a transient
          downstream failure once Day-5+ LLM calls land) would block
          every legitimate retry with the same X-Idempotency-Key for
          24 hours. Releasing the lock on failure means a client retry
          with the same key gets a fresh `acquired` decision + a fresh
          execution — exactly what F10's "default-on idempotency"
          contract promises for the failure case.

          Why DELETE not "mark as failed":
          - F10 + the contract at `interface-contracts/01-internal-rpc-contracts.md`
            don't define a "failed" cache state. Adding one would
            require a third dispatch branch in `acquire_or_check`
            (currently 4 states: acquired / replay_done /
            fingerprint_mismatch / in_flight_timeout) + new handler
            logic + new contract wording. Per A2.1: keep the smallest
            change that fixes the bug.
          - DELETE is atomic in Redis; no race with concurrent waiters
            (they'd see the key vanish + their next poll attempt
            would get a fresh `acquired` if they SET NX'd next).
          - The concurrent-waiter case is the only mild gotcha: a
            polling waiter loses its view of the in-progress state +
            its next poll-loop iteration sees the key missing. Per
            the poll-loop body in `acquire_or_check`, missing-key on
            a poll iteration is already handled gracefully — it just
            sleeps + retries until the ceiling, at which point it
            returns `in_flight_timeout`. The waiter's 503 envelope
            is the right shape for "the original request failed; you
            should retry from the top with a fresh request".
    """
    redis_client = get_redis()
    deleted_count = await redis_client.delete(redis_key)
    _log.info(
        "idempotency_lock_released_on_failure",
        extra={
            "idempotency_key_hash_prefix": _idempotency_key_hash_prefix(redis_key),
            "deleted_count": deleted_count,
        },
    )


# ===========================================================================
# RELATED FILES:
#   config.py                 — `redis_url` + `redis_sentinel_enabled` settings
#   main.py                   — init_redis() / close_redis() in lifespan
#   run_turn.py               — consumer (acquire_or_check + mark_complete)
#   models/turn.py            — `MessageResponse` the response_payload models
#   ../../shared-config.yaml  — `redis.sentinel_master_name` + `sentinel_hosts`
#                              (per C7); loaded at init_redis when the
#                              sentinel-enabled flag is on
#   ../../tests/test_run_turn.py
#                            — F10 + atomic-dedup + 409 + 503 + 400 coverage
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — coordinator-owned contract at PR #98 31d1dac
#                              spelling out C11 + atomic dedup + 400 reject
# ===========================================================================
