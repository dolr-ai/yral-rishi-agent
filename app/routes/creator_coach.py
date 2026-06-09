"""Phase 7.5 — Soul File Coach endpoints (creator-facing).

Creators chat with an AI coach to improve their bots' personality. Auth
gate on every endpoint: the creator must own the bot (or the coach
conversation tied to the bot).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import coach_repo, influencer_repo, quality_score_repo
from services import coach as coach_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator/coach", tags=["Soul File Coach"])


def _format_message(m: dict) -> dict:
    # Coach UX overhaul (2026-06-04) — surface `suggestions` on opening
    # turns so mobile can render the 3 tappable chips. asyncpg gives us
    # JSONB as a parsed list/dict already; passthrough as-is. NULL stays
    # NULL for all non-opening rows.
    suggestions = m.get("suggestions")
    if isinstance(suggestions, str):
        import json as _json

        try:
            suggestions = _json.loads(suggestions)
        except (_json.JSONDecodeError, TypeError):
            suggestions = None
    # Coach Fix 1 PR-B — surface the override blob on proposal turns
    # so mobile knows whether ✅ Save will edit system_instructions
    # (proposed_changes set) or flip an override (proposed_global_rule_override set).
    override = m.get("proposed_global_rule_override")
    if isinstance(override, str):
        import json as _json

        try:
            override = _json.loads(override)
        except (_json.JSONDecodeError, TypeError):
            override = None
    return {
        "id": str(m["id"]),
        "coach_conversation_id": str(m["coach_conversation_id"]),
        "role": m["role"],
        "content": m["content"],
        "proposed_changes": m.get("proposed_changes"),
        "proposed_global_rule_override": override,
        "reasoning": m.get("reasoning"),
        "suggestions": suggestions,
        "created_at": m["created_at"].isoformat()
        if isinstance(m["created_at"], datetime)
        else m["created_at"],
    }


async def _load_owned_bot(pool, user_id: str, bot_id: str) -> dict:
    inf = await influencer_repo.get_by_id(pool, bot_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Bot not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="You don't own this bot")
    return inf


async def _load_owned_session(pool, user_id: str, coach_conversation_id: str) -> dict:
    session = await coach_repo.get_session(pool, coach_conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="Coach session not found")
    if session["creator_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not your coach session")
    return session


@router.post("/conversations/{bot_id}", status_code=201)
async def create_coach_session(bot_id: str, request: Request):
    """Coach UX overhaul (2026-06-04) — get-or-create + coach speaks first.

    Default: returns the most recent existing session for (creator, bot)
    with `resumed: true` so mobile can re-render the conversation via
    the existing GET /messages endpoint instead of throwing the
    creator into a fresh thread every time.

    Body `{"fresh": true}` forces a brand-new session — the creator
    explicitly chose "Start over" in the UI.

    On NEW session creation only, the coach speaks first via
    `coach_opening` — opens with a warm greeting referencing the bot
    + 3 short tappable suggestion chips. Persisted as the first coach
    message with `suggestions` populated.
    """
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _load_owned_bot(pool, user_id, bot_id)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    fresh = bool(body.get("fresh"))

    if not fresh:
        existing = await coach_repo.latest_session_for_bot(pool, user_id, bot_id)
        if existing:
            return {
                "id": str(existing["id"]),
                "bot_id": existing["bot_id"],
                "bot_name": inf.get("display_name"),
                "resumed": True,
                "created_at": existing["created_at"].isoformat()
                if isinstance(existing["created_at"], datetime)
                else existing["created_at"],
            }

    session = await coach_repo.create_session(pool, user_id, bot_id)

    # Coach speaks first. Reuse the same grounding the per-turn coach
    # uses (recent conv samples + latest quality score) so the opening
    # greeting can reference real signal instead of being generic. The
    # ~2-4s latency is covered by mobile's existing "loading session"
    # state per the spec.
    try:
        recent = await pool.fetch(
            """
            SELECT m.conversation_id, m.role, m.content, m.created_at
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE c.influencer_id = $1
            ORDER BY m.created_at DESC
            LIMIT 60
            """,
            bot_id,
        )
        latest_score = await quality_score_repo.latest_for_bot(pool, bot_id)
        greeting, suggestions = await coach_service.coach_opening(
            bot_name=inf.get("display_name") or inf.get("name") or "this bot",
            bot_archetype=inf.get("category") or "general",
            current_instructions=inf.get("system_instructions") or "",
            recent_conv_rows=[dict(r) for r in recent],
            quality_score=latest_score,
        )
        await coach_repo.add_message(
            pool,
            str(session["id"]),
            "coach",
            greeting,
            suggestions=suggestions,
        )
    except Exception:
        # Opening message failure must not block session creation —
        # creator can still type. coach_opening has its own fallback
        # output; any exception above (DB/LLM) lands here and we just
        # skip the opening message persistence.
        logger.exception(
            "coach_opening failed for session %s — proceeding without opening message",
            session["id"],
        )

    return {
        "id": str(session["id"]),
        "bot_id": session["bot_id"],
        "bot_name": inf.get("display_name"),
        "resumed": False,
        "created_at": session["created_at"].isoformat()
        if isinstance(session["created_at"], datetime)
        else session["created_at"],
    }


@router.post("/conversations/{coach_conversation_id}/messages")
async def send_coach_message(coach_conversation_id: str, body: dict, request: Request):
    """Creator sends a message; coach replies with text and optionally a
    structured proposal (proposed_changes + reasoning)."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, coach_conversation_id)

    content = (body or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=422, detail="content is required")

    inf = await influencer_repo.get_by_id(pool, session["bot_id"])
    if not inf:
        raise HTTPException(status_code=410, detail="Underlying bot was deleted")

    # Save the creator's message first so the coach can see it in history
    creator_msg = await coach_repo.add_message(
        pool, coach_conversation_id, "creator", content.strip()
    )

    # Build context for the coach: prior session turns + last ~60 user-bot
    # messages across this bot's conversations (anonymized — no user_id in
    # the prompt). 60 is enough to cover 5-10 short conversations; the
    # coach service caps each line to 200 chars to stay under Gemini's
    # input budget.
    history = await coach_repo.list_messages(pool, coach_conversation_id)
    recent = await pool.fetch(
        """
        SELECT m.conversation_id, m.role, m.content, m.created_at
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
        ORDER BY m.created_at DESC
        LIMIT 60
        """,
        session["bot_id"],
    )
    recent_rows = [dict(r) for r in recent]

    # Phase 7.7 integration: pull the latest nightly quality score so the
    # coach can ground its suggestions ("is my bot any good?") in data.
    latest_score = await quality_score_repo.latest_for_bot(pool, session["bot_id"])

    # Coach UX overhaul (2026-06-04) — when mobile sets
    # request_proposal=true (the creator tapped Save), force the
    # coach to commit to a JSON proposal block this turn.
    force_proposal = bool((body or {}).get("request_proposal"))

    display, proposed, reasoning, proposed_override = await coach_service.coach_reply(
        bot_name=inf.get("display_name") or inf.get("name") or "this bot",
        bot_archetype=inf.get("category") or "general",
        current_instructions=inf.get("system_instructions") or "",
        recent_conv_rows=recent_rows,
        quality_score=latest_score,
        # Exclude the creator's just-saved message from history; it's the
        # latest_message slot in the meta-prompt instead
        session_history=[m for m in history if m["id"] != creator_msg["id"]],
        latest_message=content.strip(),
        force_proposal=force_proposal,
    )

    # Coach Fix 1 PR-B — coach_reply returns EITHER proposed_changes
    # (system_instructions edit) OR proposed_override (global_rule_overrides
    # flip), never both. Persist whichever is present; both NULL for
    # plain-text turns (clarifying question, the override-confirmation ask).
    coach_msg = await coach_repo.add_message(
        pool,
        coach_conversation_id,
        "coach",
        display,
        proposed_changes=proposed,
        reasoning=reasoning,
        proposed_global_rule_override=proposed_override,
    )
    await coach_repo.touch_session(pool, coach_conversation_id)

    return {
        "creator_message": _format_message(creator_msg),
        "coach_message": _format_message(coach_msg),
    }


@router.post("/conversations/{coach_conversation_id}/apply")
async def apply_coach_proposal(coach_conversation_id: str, request: Request):
    """Apply the most recent coach proposal to the bot's system_instructions.
    Records previous text in system_instructions_history for rollback."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, coach_conversation_id)

    proposal = await coach_repo.latest_proposal(pool, coach_conversation_id)
    if not proposal:
        raise HTTPException(
            status_code=409, detail="No proposal to apply in this session"
        )

    inf = await influencer_repo.get_by_id(pool, session["bot_id"])
    if not inf:
        raise HTTPException(status_code=410, detail="Underlying bot was deleted")

    # Coach Fix 1 PR-B — dispatch on proposal type. The override path
    # writes to ai_influencers.global_rule_overrides (JSONB merge); the
    # legacy path writes proposed_changes to system_instructions. EXACTLY
    # ONE column is populated per proposal turn (enforced by coach_reply
    # + repo.add_message contracts).
    override_raw = proposal.get("proposed_global_rule_override")
    if override_raw:
        import json as _json

        if isinstance(override_raw, str):
            try:
                override_blob = _json.loads(override_raw)
            except (_json.JSONDecodeError, TypeError):
                raise HTTPException(
                    status_code=500,
                    detail="Stored override blob is malformed; cannot apply",
                ) from None
        else:
            override_blob = override_raw
        if not isinstance(override_blob, dict) or not override_blob.get("key"):
            raise HTTPException(
                status_code=500,
                detail="Stored override blob missing 'key'; cannot apply",
            )
        key = override_blob["key"]
        value = override_blob.get("value", "set")
        # Merge into existing overrides (preserve other per-bot opts).
        # `||` JSONB-merge: right-hand-side wins on conflict.
        await pool.execute(
            """
            UPDATE ai_influencers
            SET global_rule_overrides = COALESCE(global_rule_overrides, '{}'::jsonb)
                                       || jsonb_build_object($1::text, $2::text),
                updated_at = NOW()
            WHERE id = $3
            """,
            key,
            value,
            session["bot_id"],
        )
        # History row is per-system_instructions today; we still record
        # the event for the audit trail using a sentinel "PRE→POST" pair
        # so a future migration to a typed override-history table can
        # backfill from system_instructions_history if needed.
        prev_overrides_json = _json.dumps(inf.get("global_rule_overrides") or {})
        new_overrides_json = _json.dumps(
            {**(inf.get("global_rule_overrides") or {}), key: value}
        )
        history_row = await coach_repo.record_application(
            pool,
            bot_id=session["bot_id"],
            coach_conversation_id=coach_conversation_id,
            coach_message_id=str(proposal["id"]),
            previous_instructions=f"global_rule_overrides={prev_overrides_json}",
            new_instructions=f"global_rule_overrides={new_overrides_json}",
            applied_by=user_id,
        )
        receipt_content = (
            f"✅ Saved — platform-rule override applied for "
            f"{inf.get('display_name') or 'your bot'}: "
            f"{key} = {value}. {proposal['content']}"
        )
        receipt_msg = await coach_repo.add_message(
            pool,
            coach_conversation_id,
            "coach",
            receipt_content,
        )
        return {
            "applied": True,
            "applied_type": "global_rule_override",
            "history_id": str(history_row["id"]),
            "override_key": key,
            "override_value": value,
            "applied_at": history_row["applied_at"].isoformat()
            if isinstance(history_row["applied_at"], datetime)
            else history_row["applied_at"],
            "receipt_message": _format_message(receipt_msg),
        }

    # ─── Legacy path: system_instructions edit ─────────────────────────
    previous = inf.get("system_instructions") or ""
    new_text = proposal["proposed_changes"] or ""
    if previous == new_text:
        raise HTTPException(
            status_code=409, detail="Proposed instructions equal current instructions"
        )

    # Atomic: write history first, then update bot. If the UPDATE fails we
    # have a paper trail of the intent (and the history row can be reversed
    # by hand). If history insert fails, we never touch the bot.
    history_row = await coach_repo.record_application(
        pool,
        bot_id=session["bot_id"],
        coach_conversation_id=coach_conversation_id,
        coach_message_id=str(proposal["id"]),
        previous_instructions=previous,
        new_instructions=new_text,
        applied_by=user_id,
    )
    await pool.execute(
        "UPDATE ai_influencers SET system_instructions = $1 WHERE id = $2",
        new_text,
        session["bot_id"],
    )

    # Coach UX overhaul (2026-06-04) — receipt message in the session
    # history so the apply event is visible in GET /messages on the
    # next load. Uses the proposal's `summary`-equivalent (the coach
    # message's `content`, which is the human-friendly summary the
    # coach already produced) so the receipt mentions WHAT changed.
    summary_text = proposal["content"]
    receipt_content = (
        f"✅ Saved — {inf.get('display_name') or 'your bot'}'s "
        f"personality updated: {summary_text}"
    )
    receipt_msg = await coach_repo.add_message(
        pool,
        coach_conversation_id,
        "coach",
        receipt_content,
    )

    return {
        "applied": True,
        "applied_type": "system_instructions",
        "history_id": str(history_row["id"]),
        "previous_instructions": previous,
        "new_instructions": new_text,
        "applied_at": history_row["applied_at"].isoformat()
        if isinstance(history_row["applied_at"], datetime)
        else history_row["applied_at"],
        "receipt_message": _format_message(receipt_msg),
    }


@router.get("/conversations/{coach_conversation_id}/messages")
async def list_coach_messages(coach_conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()
    await _load_owned_session(pool, user_id, coach_conversation_id)
    messages = await coach_repo.list_messages(pool, coach_conversation_id)
    return {
        "coach_conversation_id": coach_conversation_id,
        "messages": [_format_message(m) for m in messages],
        "total": len(messages),
    }
