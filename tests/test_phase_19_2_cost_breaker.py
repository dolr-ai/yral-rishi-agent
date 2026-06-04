"""Phase 19.2 — source-pin tests for the per-user daily cost breaker.

Mirrors the source-pin pattern of test_phase_23_*. The Redis integration
is verified during deploy (Redis-required integration tests are not in CI).

What we pin:
  - llm_cost_breaker module exists with required surface
  - llm_registry.call has pre-call check_or_reject hook
  - llm_registry._record_cost has post-call increment hook
  - CostCeilingExceeded exception class exists
  - Fail-open semantics documented in the module docstring
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── llm_cost_breaker module ────────────────────────────────────────────


def test_module_exists_with_required_surface():
    src = _read("app/services/llm_cost_breaker.py")
    assert "class CostCeilingExceeded(" in src
    assert "async def check_or_reject(" in src
    assert "async def increment(" in src
    assert "async def current_spend_usd(" in src
    assert "async def ceiling_for(" in src
    assert "DEFAULT_DAILY_CEILING_USD" in src


def test_default_ceiling_env_overridable():
    """Ops should be able to bump the default ceiling via env var
    without a code change."""
    src = _read("app/services/llm_cost_breaker.py")
    assert 'os.environ.get("LLM_PER_USER_DAILY_CEILING_USD"' in src


def test_check_or_reject_bypasses_background_processes():
    """user_id=None (background loops) must NOT be blocked by the
    breaker. The kill-switch framework owns background spend control;
    this breaker is for user-attributable calls only."""
    src = _read("app/services/llm_cost_breaker.py")
    pos = src.find("async def check_or_reject(")
    body = src[pos : pos + 1000]
    assert "if not user_id:" in body
    assert "return" in body


def test_check_or_reject_raises_cost_ceiling_exceeded():
    """The pre-call gate must raise (not silently swallow) when the
    user is over their ceiling. Caller catches → returns 402."""
    src = _read("app/services/llm_cost_breaker.py")
    pos = src.find("async def check_or_reject(")
    body = src[pos : pos + 1500]
    assert "raise CostCeilingExceeded(" in body


def test_fail_open_on_redis_unreachable():
    """Critical safety property: if Redis is down we MUST NOT block
    all user traffic. Better to lose a day of metered spend tracking
    than to refuse service. Pin the fail-open semantics."""
    src = _read("app/services/llm_cost_breaker.py")
    # current_spend_usd returns 0.0 on Redis-unavailable → ceiling
    # check passes → call proceeds. Pin both branches.
    cs_pos = src.find("async def current_spend_usd(")
    cs_body = src[cs_pos : cs_pos + 1000]
    assert "return 0.0" in cs_body
    assert "fail-open" in cs_body.lower() or "fail_open" in cs_body.lower()


def test_sentry_dedup_per_user_per_day():
    """If a chatty user keeps hitting the ceiling, we should fire ONE
    Sentry alert (not N). The dedup key uses NX semantics."""
    src = _read("app/services/llm_cost_breaker.py")
    pos = src.find("async def _maybe_fire_sentry(")
    body = src[pos : pos + 1500]
    assert "nx=True" in body
    assert "dedup" in body.lower()


def test_redis_key_is_day_bucketed_with_48h_ttl():
    """Day-bucket keys auto-clear via 48h TTL. Avoids needing a
    midnight-cron cleanup."""
    src = _read("app/services/llm_cost_breaker.py")
    assert 'strftime("%Y-%m-%d")' in src
    assert "48 * 3600" in src or "172800" in src


# ─── llm_registry integration ───────────────────────────────────────────


def test_registry_call_pre_check_hook():
    """The pre-call breaker check must run BEFORE the LLM dispatch.
    If the check raises, the LLM call never happens — no cost incurred
    on the rejection path."""
    src = _read("app/services/llm_registry.py")
    pos = src.find("async def call(")
    next_def = src.find("\nasync def ", pos + 1)
    body = src[pos:next_def]
    # The check must come BEFORE client_module.complete
    check_pos = body.find("llm_cost_breaker.check_or_reject(user_id)")
    complete_pos = body.find("client_module.complete(")
    assert check_pos > 0, "missing pre-call breaker check"
    assert complete_pos > check_pos, (
        "breaker check must happen BEFORE the LLM dispatch — "
        f"check at {check_pos}, complete at {complete_pos}"
    )


def test_registry_post_call_increment_hook():
    """The post-call increment must feed the Redis counter so the next
    call sees today's spend. Lives in _record_cost (success path)."""
    src = _read("app/services/llm_registry.py")
    pos = src.find("async def _record_cost(")
    next_def = src.find("\nasync def ", pos + 1)
    body = src[pos:next_def]
    assert "llm_cost_breaker.increment(user_id, cost_usd)" in body


def test_registry_increment_is_fail_safe():
    """The increment must NOT break the success path if Redis errors.
    Wrap in try/except with debug-log so logs aren't spammed."""
    src = _read("app/services/llm_registry.py")
    pos = src.find("async def _record_cost(")
    next_def = src.find("\nasync def ", pos + 1)
    body = src[pos:next_def]
    assert "except Exception" in body
    assert "non-fatal" in body
