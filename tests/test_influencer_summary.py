"""Coach Fix 2 backend — plain-English bot-summary endpoint.

Mix of behavioral (cache freshness, validator) + source-pin (route
wiring, repo helper, LLM-call shape).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── cache freshness ─────────────────────────────────────────────────────


def test_cache_is_fresh_returns_none_when_no_metadata():
    from services.influencer_summary import cache_is_fresh

    assert cache_is_fresh({}) is None
    assert cache_is_fresh({"metadata": {}}) is None
    assert cache_is_fresh({"metadata": {"other": "stuff"}}) is None


def test_cache_is_fresh_returns_cached_when_no_updated_at():
    """If the row has no updated_at we can't know if the cache is stale;
    we trust the cache rather than regenerate on every call. Reasonable
    fallback — only happens for pre-migration rows where updated_at
    wasn't set."""
    from services.influencer_summary import cache_is_fresh

    cached_summary = {"bullets": [{"text": "x", "category": "personality", "override_target": None}]}
    inf = {
        "metadata": {
            "plain_english_summary": cached_summary,
            "summary_generated_at": "2026-06-09T07:00:00+00:00",
        },
        "updated_at": None,
    }
    assert cache_is_fresh(inf) == cached_summary


def test_cache_is_fresh_returns_none_when_bot_updated_after_cache():
    """Bot was edited (updated_at advanced) after summary was generated
    → cache is stale, return None so the route regenerates."""
    from services.influencer_summary import cache_is_fresh

    generated = datetime(2026, 6, 9, 7, 0, 0, tzinfo=timezone.utc)
    updated = generated + timedelta(minutes=5)
    cached_summary = {"bullets": [{"text": "x"}]}
    inf = {
        "metadata": {
            "plain_english_summary": cached_summary,
            "summary_generated_at": generated.isoformat(),
        },
        "updated_at": updated,
    }
    assert cache_is_fresh(inf) is None


def test_cache_is_fresh_returns_cached_when_summary_newer_than_update():
    """Common case — bot stable, summary newly generated. Cache hit."""
    from services.influencer_summary import cache_is_fresh

    updated = datetime(2026, 6, 9, 7, 0, 0, tzinfo=timezone.utc)
    generated = updated + timedelta(minutes=5)
    cached_summary = {"bullets": [{"text": "x"}]}
    inf = {
        "metadata": {
            "plain_english_summary": cached_summary,
            "summary_generated_at": generated.isoformat(),
        },
        "updated_at": updated,
    }
    assert cache_is_fresh(inf) == cached_summary


def test_cache_is_fresh_handles_jsonb_as_string():
    """asyncpg returns JSONB as a string in this codebase. The
    freshness check must json.loads it. Same defensive pattern as
    soul_file._render_global_rules."""
    import json

    from services.influencer_summary import cache_is_fresh

    updated = datetime(2026, 6, 9, 7, 0, 0, tzinfo=timezone.utc)
    generated = updated + timedelta(minutes=5)
    cached_summary = {"bullets": [{"text": "x"}]}
    metadata_str = json.dumps(
        {
            "plain_english_summary": cached_summary,
            "summary_generated_at": generated.isoformat(),
        }
    )
    inf = {"metadata": metadata_str, "updated_at": updated}
    assert cache_is_fresh(inf) == cached_summary


# ─── validator behavior ──────────────────────────────────────────────────


def test_validator_accepts_well_formed_summary():
    from services.influencer_summary import _validate_summary

    parsed = {
        "bullets": [
            {"text": "Warm companion personality", "category": "personality", "override_target": None},
            {"text": "Replies in 1-3 sentences", "category": "reply_length", "override_target": "response_length"},
            {"text": "Mirrors user's language", "category": "language", "override_target": "language_mirror"},
            {"text": "Stays in character at all times", "category": "constraint", "override_target": None},
            {"text": "Warm, conversational tone", "category": "tone", "override_target": None},
        ]
    }
    out = _validate_summary(parsed)
    assert out is not None
    assert len(out["bullets"]) == 5


def test_validator_rejects_too_few_bullets():
    from services.influencer_summary import _validate_summary

    assert _validate_summary({"bullets": []}) is None
    assert _validate_summary({"bullets": [{"text": "x", "category": "personality"}, {"text": "y", "category": "tone"}]}) is None


def test_validator_drops_unknown_override_target():
    """LLM hallucinates an override slug that isn't in the registry —
    the field gets nulled rather than rejecting the whole bullet."""
    from services.influencer_summary import _validate_summary

    parsed = {
        "bullets": [
            {"text": "A", "category": "personality", "override_target": "character_consistency"},  # not overrideable
            {"text": "B", "category": "personality", "override_target": "response_length"},  # valid
            {"text": "C", "category": "personality", "override_target": "garbage"},  # invalid
            {"text": "D", "category": "tone", "override_target": None},
            {"text": "E", "category": "tone", "override_target": None},
        ]
    }
    out = _validate_summary(parsed)
    assert out is not None
    targets = [b["override_target"] for b in out["bullets"]]
    assert targets == [None, "response_length", None, None, None]


def test_validator_rejects_bullet_missing_text():
    from services.influencer_summary import _validate_summary

    parsed = {
        "bullets": [
            {"text": "A", "category": "personality"},
            {"category": "tone"},  # missing text
            {"text": "C", "category": "personality"},
            {"text": "D", "category": "constraint"},
            {"text": "E", "category": "tone"},
        ]
    }
    assert _validate_summary(parsed) is None


# ─── source-pin: route + repo wiring ─────────────────────────────────────


def test_route_endpoint_exists():
    src = (REPO / "app" / "routes" / "influencers.py").read_text()
    assert '@router.get("/influencers/{influencer_id}/summary")' in src
    assert "async def get_influencer_summary(" in src


def test_route_uses_cache_first():
    """The cached path must run BEFORE the LLM call — otherwise every
    request costs gemini cents."""
    src = (REPO / "app" / "routes" / "influencers.py").read_text()
    pos = src.find("async def get_influencer_summary(")
    body = src[pos : pos + 3000]
    cache_pos = body.find("cache_is_fresh(inf)")
    gen_pos = body.find("generate_for_influencer(inf)")
    assert cache_pos != -1, "cache check missing"
    assert gen_pos != -1, "generation call missing"
    assert cache_pos < gen_pos, "cache check must precede LLM call"


def test_route_persists_after_generation():
    """A cache miss must write the result back so the next call is a hit."""
    src = (REPO / "app" / "routes" / "influencers.py").read_text()
    pos = src.find("async def get_influencer_summary(")
    body = src[pos : pos + 3000]
    assert "cache_plain_english_summary(pool, influencer_id, summary)" in body


def test_route_returns_503_on_llm_failure():
    """LLM call can fail — we surface a 503 with a friendly message
    rather than a 500 stack trace. The mobile shows a "try again"
    affordance on 503s."""
    src = (REPO / "app" / "routes" / "influencers.py").read_text()
    pos = src.find("async def get_influencer_summary(")
    body = src[pos : pos + 3000]
    assert "status_code=503" in body


def test_route_no_auth_matches_get_influencer_pattern():
    """Summary endpoint is public — same as GET /influencers/{id}
    detail. No `get_current_user(request)` call in the handler.
    Scope to the function body (stop at next @router decorator)."""
    src = (REPO / "app" / "routes" / "influencers.py").read_text()
    start = src.find("async def get_influencer_summary(")
    end = src.find("@router.", start + 1)
    body = src[start:end]
    assert "get_current_user" not in body


def test_repo_cache_helper_uses_jsonb_merge():
    """The cache write must preserve other keys in metadata. `||`
    JSONB-merge keeps everything else.

    Scope to the SQL block only (between the triple-quote markers of
    the pool.execute string literal) so we don't accidentally include
    neighboring functions like soft_delete that legitimately touch
    updated_at."""
    src = (REPO / "app" / "repositories" / "influencer_repo.py").read_text()
    assert "async def cache_plain_english_summary(" in src
    func_start = src.find("async def cache_plain_english_summary(")
    sql_open = src.find('"""', src.find("pool.execute(", func_start))
    sql_close = src.find('"""', sql_open + 3)
    sql_body = src[sql_open:sql_close]
    # Merge into metadata, not replace it
    assert "COALESCE(metadata, '{}'::jsonb)" in sql_body
    assert "jsonb_build_object" in sql_body
    # Both keys present
    assert "'plain_english_summary'" in sql_body
    assert "'summary_generated_at'" in sql_body
    # The SQL must NOT touch updated_at — that would immediately
    # invalidate the cache we just wrote.
    assert "updated_at" not in sql_body


def test_service_uses_soul_file_compose_with_overrides():
    """The summary must reflect the EFFECTIVE prompt (with overrides),
    not just system_instructions. Otherwise bullets would describe
    behavior the bot doesn't actually have."""
    src = (REPO / "app" / "services" / "influencer_summary.py").read_text()
    assert "soul_file.compose(" in src
    assert "global_rule_overrides=inf.get(" in src


def test_service_overrideable_slugs_sourced_from_soul_file():
    """Single source of truth — overrideable list comes from
    GLOBAL_RULES_OVERRIDEABLE. Adding a key there auto-propagates."""
    src = (REPO / "app" / "services" / "influencer_summary.py").read_text()
    assert "GLOBAL_RULES_OVERRIDEABLE" in src
    assert "soul_file" in src
