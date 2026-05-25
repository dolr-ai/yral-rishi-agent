# ---------------------------------------------------------------------------
# test_redis_client_safety_gates.py — unit tests for the redis_client
# safety gates Codex PR #151 round-2 CONCERN 2 named.
#
# ⭐ START HERE: 5 focused tests covering 2 safety-gate code paths in
# `app/redis_client.py`:
#
#   1. verify_production_sentinel_or_die() raises RuntimeError when
#      ENVIRONMENT=production AND REDIS_SENTINEL_ENABLED=false (the
#      C11 deployed-environment fail-closed path; PR #151 round-5
#      switched from SystemExit(1) → RuntimeError per coordinator
#      snippet pattern so the FastAPI lifespan's try/except can
#      propagate cleanly).
#   2. verify_production_sentinel_or_die() raises RuntimeError when
#      ENVIRONMENT=staging AND REDIS_SENTINEL_ENABLED=false. Same C11
#      gate; PR #151 round-6 BLOCKER 1 broadened the gate's
#      environment check from {"production"} only to
#      {"production", "staging"} per F4 + C11 (both deployed envs
#      share the HA Redis Sentinel infrastructure on rishi-4/5/6).
#   3. verify_production_sentinel_or_die() does NOT raise when
#      ENVIRONMENT=local AND REDIS_SENTINEL_ENABLED=false (local dev
#      is allowed to fall back; the gate only fires for envs in the
#      DEPLOYED-ENVIRONMENTS set).
#   4. init_redis() raises RuntimeError when REDIS_SENTINEL_ENABLED=true
#      but shared-config.yaml's `redis.sentinel_master_name` is empty
#      (sentinel-config-parsing-missing-master path).
#   5. init_redis() raises RuntimeError when REDIS_SENTINEL_ENABLED=true
#      but shared-config.yaml's `redis.sentinel_hosts` is empty (the
#      other half of the sentinel-config-parsing-missing path).
#
# WHY MOCK `_load_redis_section_from_shared_config` INSTEAD OF tmpdir
# YAML files
# The shared-config-on-disk path reads `pathlib.Path(__file__).parent
# .parent / "shared-config.yaml"` — i.e., module-relative. Testing with
# tmpdir requires either symlinking the module location OR monkey-
# patching the module's `__file__`, both of which are brittle. Mocking
# the support function directly is simpler + tests the SAME failure
# path init_redis takes when the on-disk file is malformed (the support
# function's output is what init_redis validates, regardless of where
# that output came from).
#
# WHY MONKEYPATCH ENVIRONMENT VARIABLES (vs constructing Settings(...) directly)
# pydantic-settings respects environment variables + .env files.
# Monkeypatching environment variables + clearing the get_settings
# cache (via the autouse conftest fixture) means tests exercise the
# SAME code path production startup takes. Constructing Settings(...)
# directly would bypass the environment-variable parsing entirely +
# leave a gap where a future environment-variable-parsing regression
# slipped through.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# pytest fixtures + pytest.raises context manager.
import pytest

# Module under test — referenced as a module so monkeypatch.setattr
# can target `app.redis_client.NAME` rather than the imported alias.
# The autouse fixture in conftest.py resets `app.redis_client._redis`
# to None between every test.
import app.redis_client as redis_client_module

# Direct imports for the two functions under test — clearer in the
# test function bodies than `redis_client_module.verify_...` at every
# call site.
from app.redis_client import init_redis, verify_production_sentinel_or_die


# ===========================================================================
# verify_production_sentinel_or_die() — C11 production-fail-closed gate
# ===========================================================================


def test_verify_production_sentinel_or_die_raises_when_production_without_sentinel(
    monkeypatch,
):
    """Production env + REDIS_SENTINEL_ENABLED=false → RuntimeError.

    WHAT: monkeypatches ENVIRONMENT=production + REDIS_SENTINEL_ENABLED=
          false, calls verify_production_sentinel_or_die, asserts the
          function raises RuntimeError with a message naming the
          REDIS_SENTINEL_ENABLED + environment values.
    WHEN: this is THE production-safety gate Codex PR #97 round-5
          ITEM 6 + Session 4's PR #96 round-4 + PR #151 round-5
          BLOCKER 1 patterned. A regression that softens it to a
          warning (per the pre-Codex-round-4 idempotency.py) would
          let a misconfigured production deploy silently fall back
          to single-primary Redis — exactly the C11 violation the
          gate exists to prevent.
    WHY:  this test fires the moment the production-fail-closed gate
          softens in a future refactor. Without the test, a code
          change like `if env=="production"+not sentinel: log.warning(...)`
          (instead of `raise RuntimeError`) would pass the spawn-
          smoke gate (spawn-smoke runs `environment=local`, never
          hits the gate) + ship silently to production.

    Round-5 (PR #151 BLOCKER 1): the gate used to raise SystemExit(1);
    round-5 switched to RuntimeError so the FastAPI lifespan's
    try/except propagates cleanly + tests can assert on exception
    class without monkey-patching sys.exit.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "false")

    # Assert the raised RuntimeError mentions REDIS_SENTINEL_ENABLED
    # in its message — that's the operator-actionable token the
    # error contract surfaces. A future refactor that produced a
    # generic-message RuntimeError would still pass `pytest.raises
    # (RuntimeError)` but should fail the match assertion.
    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_ENABLED"):
        verify_production_sentinel_or_die()


def test_verify_production_sentinel_or_die_raises_when_staging_without_sentinel(
    monkeypatch,
):
    """Staging env + REDIS_SENTINEL_ENABLED=false → RuntimeError.

    WHAT: monkeypatches ENVIRONMENT=staging + REDIS_SENTINEL_ENABLED=
          false, calls verify_production_sentinel_or_die, asserts the
          function raises RuntimeError with a message naming the
          REDIS_SENTINEL_ENABLED + environment values.
    WHEN: PR #151 round-5 BLOCKER 1 (F4/C11): staging runs on the
          same HA Redis Sentinel infrastructure as production per
          F4 + C11. A staging deploy with sentinel disabled has the
          same C11-violation + failover-safety-lost risk as
          production.
    WHY:  before round-5 BLOCKER 1's fix, the gate's environment
          check was `env == "production"` — staging slipped past
          silently. This test fires if a future refactor narrows
          the deployed-environments set back to {"production"} only
          (e.g., a copy-paste from an older service that hasn't
          internalised the round-5 fix). Matched-pair coverage with
          the production-case test above.
    """
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "false")

    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_ENABLED"):
        verify_production_sentinel_or_die()


def test_verify_production_sentinel_or_die_allows_local_without_sentinel(
    monkeypatch,
):
    """Local env + REDIS_SENTINEL_ENABLED=false → no raise (local OK).

    WHAT: monkeypatches ENVIRONMENT=local + REDIS_SENTINEL_ENABLED=
          false, calls verify_production_sentinel_or_die, asserts
          the function returns cleanly.
    WHEN: this is the positive-case complement to the production +
          staging tests above. The gate's input check is
          `environment in {"production", "staging"}` — local + any
          other value SHOULD pass through cleanly so dev + CI
          environments can run with single-primary Redis.
    WHY:  a future refactor that accidentally broadened the gate
          (e.g., `if not sentinel_enabled: raise RuntimeError(...)`
          without the environment-set check) would block every
          local-dev run + every spawn-smoke CI invocation. This
          test catches that regression too.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "false")

    # No `pytest.raises` — the call should return None cleanly. If
    # the function raises RuntimeError (or any other exception)
    # here, pytest fails the test automatically with the raised
    # exception's traceback.
    verify_production_sentinel_or_die()


# ===========================================================================
# init_redis() — sentinel-config-parsing safety gate
# ===========================================================================


async def test_init_redis_raises_when_sentinel_enabled_but_master_name_missing(
    monkeypatch,
):
    """Sentinel enabled + sentinel_master_name missing → RuntimeError.

    WHAT: monkeypatches REDIS_SENTINEL_ENABLED=true +
          ENVIRONMENT=staging (so verify_production_sentinel_or_die
          doesn't fire first), mocks
          `_load_redis_section_from_shared_config()` to return a
          section with sentinel_hosts populated but
          sentinel_master_name empty, calls init_redis, asserts
          RuntimeError naming the missing key.
    WHEN: this is the half of the sentinel-config-missing failure
          path Codex named verbatim ("missing the
          redis.sentinel_master_name ... keys"). The check is in
          init_redis after the support function returns, not in the
          support function itself.
    WHY:  if a future refactor merges the validation into the support
          function OR removes it entirely, this test fires. Without the
          test, a production deploy with `redis_sentinel_enabled=
          true` but a typo'd / missing sentinel_master_name in
          shared-config.yaml would silently fall through to an
          uninformative AttributeError or a stale-master-discovery
          at the first Redis command.
    """
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "true")
    # Staging instead of local because the production gate would fire
    # first on production — we want to reach init_redis, not exit at
    # verify_production_sentinel_or_die.
    monkeypatch.setenv("ENVIRONMENT", "staging")

    # Mock the on-disk shared-config.yaml load: return a section with
    # sentinel_hosts but no sentinel_master_name. init_redis's
    # validation is `if not master_name or not raw_hosts` — testing
    # the master_name-missing branch isolates it from the hosts-
    # missing branch (next test).
    monkeypatch.setattr(
        "app.redis_client._load_redis_section_from_shared_config",
        lambda: {
            "sentinel_master_name": "",
            "sentinel_hosts": [{"host": "redis-sentinel-1", "port": 26379}],
        },
    )

    # The RuntimeError's message names BOTH `sentinel_master_name`
    # AND `sentinel_hosts` (since the same error covers either
    # being empty) — match on the substring that appears in the
    # current implementation. If the message changes, the test
    # author must update this match string + re-verify the
    # error-path code reads well.
    with pytest.raises(RuntimeError, match="sentinel_master_name"):
        await init_redis()


async def test_init_redis_raises_when_sentinel_enabled_but_sentinel_hosts_missing(
    monkeypatch,
):
    """Sentinel enabled + sentinel_hosts empty → RuntimeError.

    WHAT: monkeypatches REDIS_SENTINEL_ENABLED=true +
          ENVIRONMENT=staging, mocks the shared-config support
          function to return a section with sentinel_master_name set
          but sentinel_hosts empty, calls init_redis, asserts
          RuntimeError.
    WHEN: this is the other half of the sentinel-config-missing
          failure path Codex named. Same code line as the previous
          test's check, different branch of the `if not X or not Y`
          condition.
    WHY:  matched-pair coverage with the previous test: if the
          validation regresses to check only one key (e.g.,
          `if not master_name` without the hosts half), this test
          fires while the previous test passes — pinpointing the
          regression to the hosts half rather than the whole gate.
    """
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "staging")

    monkeypatch.setattr(
        "app.redis_client._load_redis_section_from_shared_config",
        lambda: {
            "sentinel_master_name": "yral-v2-redis-primary",
            "sentinel_hosts": [],
        },
    )

    with pytest.raises(RuntimeError, match="sentinel_hosts"):
        await init_redis()


# ===========================================================================
# RELATED FILES:
#   conftest.py                   — autouse fixtures clearing
#                                   get_settings() lru_cache + resetting
#                                   _redis module singleton between
#                                   every test
#   ../app/redis_client.py        — module under test:
#                                     verify_production_sentinel_or_die,
#                                     init_redis,
#                                     _load_redis_section_from_shared_config
#   ../app/config.py              — Settings model + get_settings()
#                                   cache invalidated by conftest fixture
#   ../pyproject.toml             — declares pytest + pytest-asyncio +
#                                   [tool.pytest.ini_options] config
#                                   (asyncio_mode=auto so async def
#                                   tests don't need @pytest.mark.asyncio)
# ===========================================================================
