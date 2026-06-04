"""Phase 22.3 — source-pin tests for the video-ideas backend.

Mirrors the source-pin pattern used in test_phase_23_* and
test_coach_ux_overhaul.py. Live behavior is verified by the
verification curls in the PR description against the deployed image;
these pins guard the wiring against accidental rewiring (loop dropped,
endpoints renamed, kill-switch missing, etc.).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── Migration shape ─────────────────────────────────────────────────────


def test_migration_032_table_shape():
    src = _read("migrations/032_video_ideas.sql")
    assert "CREATE TABLE IF NOT EXISTS video_ideas" in src
    for col in (
        "influencer_id TEXT NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE",
        "batch_date    DATE NOT NULL",
        "rank          SMALLINT NOT NULL",
        "hook          TEXT NOT NULL",
        "idea_text     TEXT NOT NULL",
        "status        TEXT NOT NULL DEFAULT 'fresh'",
        "used_at       TIMESTAMPTZ",
        "UNIQUE (influencer_id, batch_date, rank)",
    ):
        assert col in src, f"missing column or constraint: {col!r}"
    assert "idx_video_ideas_influencer_recent" in src
    assert "ON video_ideas (influencer_id, created_at DESC)" in src


# ─── Kill switch ────────────────────────────────────────────────────────


def test_kill_switch_registers_video_ideas():
    src = _read("app/kill_switch.py")
    assert '"video_ideas":' in src
    assert "ENABLE_VIDEO_IDEAS_LOOP" in src


# ─── LLM registry process ───────────────────────────────────────────────


def test_registry_registers_video_idea_generation():
    src = _read("app/services/llm_registry.py")
    assert '"video_idea_generation"' in src
    pos = src.find('"video_idea_generation":')
    body = src[pos : pos + 700]
    # Default per Rishi's call: internal_vllm (cheap background path).
    assert '"provider": "internal_vllm"' in body


# ─── Repository ─────────────────────────────────────────────────────────


def test_repo_has_three_required_helpers():
    src = _read("app/repositories/video_idea_repo.py")
    assert "async def insert_batch(" in src
    assert "async def latest_batch_for_bot(" in src
    assert "async def mark_used(" in src
    assert "async def bot_has_batch_for_date(" in src


def test_insert_batch_is_idempotent():
    """The nightly cron is allowed to re-run safely. Pin the ON CONFLICT
    clause so a refactor can't drop it."""
    src = _read("app/repositories/video_idea_repo.py")
    assert "ON CONFLICT (influencer_id, batch_date, rank) DO NOTHING" in src


def test_latest_batch_for_bot_uses_recent_index_path():
    """The most-recent-batch query must take the latest batch_date by
    created_at ordering — not just any batch. The CTE pin guarantees
    the index `idx_video_ideas_influencer_recent` carries the query."""
    src = _read("app/repositories/video_idea_repo.py")
    pos = src.find("async def latest_batch_for_bot(")
    body = src[pos : pos + 1500]
    assert "ORDER BY created_at DESC" in body
    assert "LIMIT 1" in body
    assert "ORDER BY rank ASC" in body  # rank-stable mobile rendering


def test_mark_used_is_idempotent_for_already_used():
    """Re-flipping a 'used' row should return the existing state, NOT
    re-stamp used_at and NOT 404. Pin both branches."""
    src = _read("app/repositories/video_idea_repo.py")
    pos = src.find("async def mark_used(")
    body = src[pos : pos + 2000]
    assert "AND status = 'fresh'" in body
    assert "already used" in body.lower() or "already_used" in body.lower()


# ─── Service / loop ─────────────────────────────────────────────────────


def test_service_exposes_three_required_surfaces():
    src = _read("app/services/video_ideas.py")
    assert "async def generate_for_one_bot(" in src
    assert "async def generate_all_once(" in src
    assert "async def video_ideas_loop(" in src


def test_loop_gates_on_kill_switch():
    src = _read("app/services/video_ideas.py")
    pos = src.find("async def video_ideas_loop(")
    body = src[pos : pos + 2000]
    assert 'is_enabled("video_ideas")' in body
    # Exception path uses logger.exception (matches the streak_tracker
    # logging hygiene fix so empty errors still leave a traceback).
    assert "logger.exception(" in body


def test_generate_all_skips_bots_with_existing_batch():
    """Re-running the cron in-day must not double-write. Pin the
    `bot_has_batch_for_date` guard."""
    src = _read("app/services/video_ideas.py")
    pos = src.find("async def generate_all_once(")
    body = src[pos : pos + 2000]
    assert 'bot_has_batch_for_date(pool, bot["id"], today)' in body


def test_active_bots_filter_recent_traffic():
    """ACTIVE_BOT_WINDOW_DAYS gate must be present and the SELECT
    must join messages so we exclude dormant bots."""
    src = _read("app/services/video_ideas.py")
    assert "ACTIVE_BOT_WINDOW_DAYS" in src
    pos = src.find("async def _list_active_bots(")
    body = src[pos : pos + 1500]
    assert "JOIN conversations c ON c.influencer_id = i.id" in body
    assert "JOIN messages m ON m.conversation_id = c.id" in body
    assert "is_active = 'active'" in body


def test_generation_prompt_constrains_json_shape():
    """The LLM is told to emit a bare JSON array with the expected
    object shape. Pin so a future contributor doesn't loosen it."""
    src = _read("app/services/video_ideas.py")
    pos = src.find("GENERATION_PROMPT")
    body = src[pos : pos + 3000]
    assert '"hook"' in body
    assert '"idea_text"' in body
    assert "ONLY a JSON array" in body or "JSON array" in body
    assert "no markdown fences" in body.lower() or "no preamble" in body.lower()


# ─── Routes ─────────────────────────────────────────────────────────────


def test_route_get_video_ideas_exists_and_is_owner_only():
    src = _read("app/routes/influencers.py")
    assert '@router.get("/influencers/{influencer_id}/video-ideas")' in src
    pos = src.find("async def list_video_ideas(")
    body = src[pos : pos + 3500]
    assert "parent_principal_id" in body
    # 403 on non-owner
    assert "Only the creator can see this influencer's ideas" in body


def test_route_get_cold_start_generates_on_demand():
    src = _read("app/routes/influencers.py")
    pos = src.find("async def list_video_ideas(")
    body = src[pos : pos + 3500]
    assert "video_ideas_service.generate_for_one_bot(pool, dict(inf))" in body
    # Cold-start gen failure must NOT 5xx the endpoint — fall through
    # to empty list so mobile gets a renderable response.
    assert "except Exception:" in body


def test_route_post_used_exists_and_is_owner_only():
    src = _read("app/routes/influencers.py")
    assert (
        '@router.post("/influencers/{influencer_id}/video-ideas/{idea_id}/used")' in src
    )
    pos = src.find("async def mark_video_idea_used(")
    body = src[pos : pos + 2000]
    assert "parent_principal_id" in body
    assert "Only the creator can mark ideas used" in body
    # Belt-and-suspenders: idea must belong to the claimed influencer.
    assert 'row["influencer_id"] != influencer_id' in body


# ─── main.py loop registration ──────────────────────────────────────────


def test_main_registers_video_ideas_loop():
    src = _read("app/main.py")
    assert "from services.video_ideas import video_ideas_loop" in src
    assert "video_ideas_task = asyncio.create_task(video_ideas_loop())" in src
    # Symmetric cancel + await on shutdown
    assert "video_ideas_task.cancel()" in src
    assert "await video_ideas_task" in src
