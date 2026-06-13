"""Phase 21αβ.H2 PR 1 — billing.yral.com client + Redis caching.

Source-pin tests cover the module shape + the fail-open / cache-TTL
contract pinned in the brief (~/.claude/plans/h2-server-side-billing-
paywall-brief-2026-06-11.md). Httpx / asyncpg / Sentry aren't in the
local venv — wire-level tests run in CI / prod.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "app" / "services" / "billing_client.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── module shape ────────────────────────────────────────────────────────


def test_module_exists():
    assert MODULE.exists(), "billing_client.py missing"


def test_check_chat_access_signature():
    """Mobile contract: (user_id, bot_id) → ChatAccessResult. Pin both
    the parameter names and the return type so the call-sites in
    chat.py (PR 2 + PR 3) stay aligned with the helper."""
    src = MODULE.read_text()
    assert (
        "async def check_chat_access(user_id: str, bot_id: str) -> ChatAccessResult:"
        in src
    )


def test_chat_access_result_carries_tri_state():
    """ChatAccessResult fields: has_access (the answer), cache_hit
    (cheap-or-not), fail_open (did we actually verify or pass through).
    Sentry tagging + the future /admin/billing-cache dashboard depend
    on all three."""
    src = MODULE.read_text()
    assert "class ChatAccessResult" in src
    for slot in ("has_access", "cache_hit", "fail_open"):
        assert slot in src, f"ChatAccessResult missing field: {slot}"


# ─── upstream contract (matches mobile + brief §2 byte-for-byte) ────────


def test_billing_url_constant_uses_check_path():
    """Path MUST equal `/google/chat-access/check` — that's what mobile
    calls. A typo here means server + mobile decisions diverge."""
    src = MODULE.read_text()
    assert 'CHECK_PATH = "/google/chat-access/check"' in src


def test_upstream_call_uses_user_id_and_bot_id_query_params():
    """Mobile sends `user_id` + `bot_id`. NOT principal_id, NOT
    influencer_id. Pin the param names so a future refactor can't
    rename to snake-case + silently break the upstream contract."""
    src = MODULE.read_text()
    assert '"user_id": user_id, "bot_id": bot_id' in src


def test_upstream_response_parses_data_has_access():
    """Response is nested under `data.has_access`. A flat
    `body.get('has_access')` parse would silently miss the truthy
    answer + fail-open every request — pin the nested path."""
    src = MODULE.read_text()
    assert '(body.get("data") or {}).get("has_access")' in src


def test_upstream_timeout_is_3_seconds():
    """Chat-send already takes 1-3s at the LLM hop. Brief says 3s
    upstream cap. Pin the literal so a future bump is a deliberate
    review-gated change."""
    src = MODULE.read_text()
    assert "BILLING_TIMEOUT_SEC = 3.0" in src
    assert "timeout=BILLING_TIMEOUT_SEC" in src


# ─── cache TTL contract ─────────────────────────────────────────────────


def test_positive_cache_ttl_60_seconds():
    src = MODULE.read_text()
    assert "DEFAULT_CACHE_TTL_SEC = 60" in src


def test_negative_cache_ttl_30_seconds():
    """30s shorter than positive so a user who just paid recovers
    within half a minute. Pin both halves of the TTL dispatch:
    constant + the conditional that picks which TTL to use."""
    src = MODULE.read_text()
    assert "NEGATIVE_CACHE_TTL_SEC = 30" in src
    assert "DEFAULT_CACHE_TTL_SEC if has_access else NEGATIVE_CACHE_TTL_SEC" in src


def test_cache_key_namespace_pinned():
    """Namespace prevents collisions with other Redis users (session,
    rate-limit, llm_routing). Pin the literal."""
    src = MODULE.read_text()
    assert 'CACHE_KEY_PREFIX = "chat_access:"' in src
    # Key construction uses both user + bot
    assert 'key = f"{CACHE_KEY_PREFIX}{user_id}:{bot_id}"' in src


# ─── fail-open posture ─────────────────────────────────────────────────


def test_fail_open_on_upstream_error():
    """The intentional fail-open: any error from the upstream call
    falls through to `has_access=True` with `fail_open=True`. Source-
    pin the exception handler + the fail-open return so a future
    refactor can't accidentally introduce fail-closed semantics that
    take down chat during a billing outage."""
    src = MODULE.read_text()
    # Exception catch is broad on purpose — anything stopping us from
    # getting a real answer means we don't have an answer
    assert "except Exception as e:" in src
    # The fail-open return shape
    assert "ChatAccessResult(has_access=True, cache_hit=False, fail_open=True)" in src


def test_fail_open_does_not_cache_the_pass_through():
    """A fail-open allow is NOT a real positive answer. Caching it
    would mean a 60s billing outage poisons the cache for 60s after
    recovery. Pin: the cache_set path is INSIDE the try, after the
    real-response parse — not after the fail-open return."""
    src = MODULE.read_text()
    # _cache_set is only called after we got a real response — pin the
    # call sits between resp.raise_for_status() and the success return
    cache_set_pos = src.find("await _cache_set(key, has_access, ttl)")
    raise_pos = src.find("resp.raise_for_status()")
    success_return_pos = src.find(
        "return ChatAccessResult(has_access=has_access, cache_hit=False)"
    )
    fail_open_return_pos = src.find(
        "return ChatAccessResult(has_access=True, cache_hit=False, fail_open=True)"
    )
    assert cache_set_pos != -1
    assert raise_pos != -1
    assert success_return_pos != -1
    assert fail_open_return_pos != -1
    # cache_set sits between raise_for_status and the success return
    assert raise_pos < cache_set_pos < success_return_pos
    # and BEFORE the fail-open branch (the fail-open path never sets cache)
    assert cache_set_pos < fail_open_return_pos


def test_fail_open_captures_to_sentry_with_billing_tags():
    """Sentry capture lets a sustained outage page someone without
    grepping logs. Brief §11: include billing.* tags so triage can
    filter by principal_id / bot_id immediately."""
    src = MODULE.read_text()
    assert "sentry_sdk.capture_message" in src
    assert 'scope.set_tag("billing.principal_id", user_id)' in src
    assert 'scope.set_tag("billing.bot_id", bot_id)' in src
    assert 'scope.set_tag("billing.outcome", "fail_open")' in src
    # Sentry itself failing must not block chat
    assert (
        "Sentry being unavailable must NEVER block chat" in src
        or "except Exception:\n            # Sentry" in src
    )


# ─── Redis client pattern matches session_memory ────────────────────────


def test_redis_client_lazy_initialized():
    """`_get_redis()` returns a memoized client. Mirrors the
    session_memory pattern so we share the connection-pool ergonomics
    + the file-first URL resolution (Swarm secret)."""
    src = MODULE.read_text()
    assert "_redis_client = None" in src
    assert "async def _get_redis():" in src
    assert "from redis_config import get_redis_url" in src


def test_redis_unreachable_degrades_to_no_cache():
    """A Redis outage must NOT take down billing checks. Pin:
    _cache_get returns None on Redis-down (→ caller treats as miss →
    falls through to upstream), _cache_set silently returns on
    Redis-down (→ caller's positive answer simply doesn't get cached
    that round)."""
    src = MODULE.read_text()
    # _cache_get returns None on Redis-down
    cache_get_pos = src.find("async def _cache_get(key: str)")
    cache_get_block = src[cache_get_pos : cache_get_pos + 800]
    assert "if r is None:\n        return None" in cache_get_block
    # _cache_set early-returns on Redis-down
    cache_set_pos = src.find("async def _cache_set(key: str")
    cache_set_block = src[cache_set_pos : cache_set_pos + 800]
    assert "if r is None:\n        return" in cache_set_block


# ─── cache hit short-circuits upstream ────────────────────────────────


def test_cache_hit_returns_before_upstream_call():
    """A cache hit MUST short-circuit the httpx call — that's the
    whole point of caching. Pin: the cache lookup happens BEFORE the
    try/except wrapping the httpx call, and the hit-branch returns."""
    src = MODULE.read_text()
    fn_pos = src.find("async def check_chat_access(")
    body = src[fn_pos : fn_pos + 3000]
    cache_get_pos = body.find("cached = await _cache_get(key)")
    try_pos = body.find("try:")
    return_cached_pos = body.find(
        "return ChatAccessResult(has_access=cached, cache_hit=True)"
    )
    assert cache_get_pos != -1
    assert try_pos != -1
    assert return_cached_pos != -1
    # Cache lookup BEFORE upstream call
    assert cache_get_pos < try_pos
    # Hit branch returns BEFORE upstream call
    assert return_cached_pos < try_pos


# ─── contract reference ────────────────────────────────────────────────


def test_module_references_brief_for_contract_anchor():
    """The brief is the spec for fail-open + TTL + Sentry tags. Anchor
    the link so a future reader can find the contract from the code."""
    src = MODULE.read_text()
    assert "21αβ.H2" in src or "Phase 21αβ.H2" in src
