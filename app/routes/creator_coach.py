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
from services import coach_intent

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
    # Coach Bucket 2 (2026-06-12 follow-up): surface the section-change
    # blob on proposal turns so mobile renders the badge ("Coach proposed
    # an edit to **Voice and tone**") from the snapshot fields without a
    # re-lookup into the live sections array. Same JSONB-string coercion
    # as the override blob — asyncpg's codec behaviour is identical.
    # Without this, the JSONB lived in coach_messages but the GET
    # /messages response dropped it on the floor.
    section_change = m.get("proposed_section_change")
    if isinstance(section_change, str):
        import json as _json

        try:
            section_change = _json.loads(section_change)
        except (_json.JSONDecodeError, TypeError):
            section_change = None
    # Coach PR-3 (migration 035) — surface the proposal lifecycle status
    # so mobile can render active/passive/applied/discarded card states.
    # 'na' rows (creator messages, opening greetings, receipts) are
    # passed through unchanged; mobile treats anything except 'pending'
    # as a non-actionable card.
    return {
        "id": str(m["id"]),
        "coach_conversation_id": str(m["coach_conversation_id"]),
        "role": m["role"],
        "content": m["content"],
        "proposed_changes": m.get("proposed_changes"),
        "proposed_global_rule_override": override,
        "proposed_section_change": section_change,
        "reasoning": m.get("reasoning"),
        "suggestions": suggestions,
        "status": m.get("status") or "na",
        "status_changed_at": m["status_changed_at"].isoformat()
        if isinstance(m.get("status_changed_at"), datetime)
        else m.get("status_changed_at"),
        "created_at": m["created_at"].isoformat()
        if isinstance(m["created_at"], datetime)
        else m["created_at"],
    }


def _ensure_section_snapshots(section_change: dict, inf: dict) -> dict:
    """Defensive snapshot injection (PR #361 contract refinement).

    Per the contract, every `proposed_section_change` blob carries:
      - section_id (load-bearing — resolves to live row at apply time)
      - section_heading (snapshot — mobile's badge label)
      - section_editable (snapshot — mobile's "can edit" gate)
      - new_body (load-bearing — the proposed text)
      - previous_body_sha256 (optimistic-concurrency handle)

    The META_PROMPT (services/coach.py SECTION_RULES_ADDENDUM) tells
    Coach to emit all five, but LLMs forget fields. When Coach omits
    `section_heading` or `section_editable`, look them up on the live
    sections array (already in `inf`) and inject before persistence —
    mobile then renders the badge from a complete blob without needing
    to re-fetch the live row.

    Live values are authoritative AT APPLY TIME (the /apply endpoint
    resolves section_id against the live row + checks the body sha).
    The snapshots are purely a render-time convenience for mobile — they
    let the badge render WITHOUT a re-fetch of the live sections array.

    We only fill MISSING fields. A Coach-emitted heading/editable stays
    intact (the contract says snapshots reflect "what Coach read," which
    can legitimately differ from the current live row if the creator
    renamed mid-session).

    Idempotent: re-call with the same input produces the same output.
    Safe on a `section_id` that no longer exists on the live row
    (returns the blob unchanged).
    """
    import json as _json

    section_id = section_change.get("section_id")
    if not isinstance(section_id, str) or not section_id.strip():
        return section_change

    needs_heading = not isinstance(section_change.get("section_heading"), str)
    needs_editable = not isinstance(section_change.get("section_editable"), bool)
    if not (needs_heading or needs_editable):
        return section_change

    live_sections = inf.get("system_instructions_sections")
    if isinstance(live_sections, str):
        try:
            live_sections = _json.loads(live_sections)
        except (_json.JSONDecodeError, TypeError):
            live_sections = []
    if not isinstance(live_sections, list):
        return section_change

    for sec in live_sections:
        if isinstance(sec, dict) and sec.get("id") == section_id:
            if needs_heading and isinstance(sec.get("heading"), str):
                section_change["section_heading"] = sec["heading"]
            if needs_editable:
                section_change["section_editable"] = bool(sec.get("editable", True))
            break
    return section_change


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
        # Strategy doc Item C — 60 → 20 trim. coach._format_conv_excerpt
        # already caps to 10 conversations × 6 turns; the prior 60 was
        # over-fetching by 3x with no signal gain. The trim saves 1-2s
        # of asyncpg row materialization on the largest-history bots.
        recent = await pool.fetch(
            """
            SELECT m.conversation_id, m.role, m.content, m.created_at
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE c.influencer_id = $1
            ORDER BY m.created_at DESC
            LIMIT 20
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

    # Coach Fix 4 (2026-06-09) — action-verb fast path. If the creator
    # typed a short action verb like "save it" or "discard" AND there's
    # a pending unapplied proposal in this session, skip the Coach LLM
    # cycle entirely and return {type: action, ...}. Mobile reads this
    # and triggers the existing /apply or /discard flow without
    # rendering another Coach reply.
    #
    # Saikat 2026-06-09: after Coach showed ✅ Saved he typed "Save
    # these changes." and Coach treated it as a NEW edit request,
    # producing more proposed_changes — the "infinite loop" feel.
    # The pending-proposal guard means we only short-circuit when
    # there's actually something to act on. If matched but no pending
    # (Saikat's exact case), we fall through to Coach LLM which has
    # the receipt in history and can answer sensibly.
    intent = coach_intent.classify_intent(content.strip())
    if intent is not None:
        pending = await coach_repo.pending_proposal(pool, coach_conversation_id)
        if pending is not None:
            return {
                "type": "action",
                "action": intent,
                "pending_proposal_id": str(pending["id"]),
                "creator_message": _format_message(creator_msg),
                "coach_message": None,
                # 2026-06-11 PR-4 — pending_proposal_exists is a top-level
                # constant of the contract. Always present, even on the
                # action short-circuit, so mobile doesn't have to special-
                # case the response shape.
                "pending_proposal_exists": True,
            }
        # No pending proposal — fall through to Coach LLM. It has the
        # session history (including any prior receipt) and can ask
        # the right clarifying question ("there's nothing pending to
        # save — what would you like to change?").

    # Build context for the coach: prior session turns + last ~20 user-bot
    # messages across this bot's conversations (anonymized — no user_id in
    # the prompt). The Coach service's `_format_conv_excerpt` already caps
    # the rendered window to 10 conversations × 6 turns; 20 rows comfortably
    # covers that without over-fetching. Strategy doc Item C — was 60 pre
    # 2026-06-12; the 3x trim saves 1-2s of asyncpg row materialization on
    # big-history sessions with zero signal loss because the downstream
    # render-time cap was already the binding constraint.
    history = await coach_repo.list_messages(pool, coach_conversation_id)
    recent = await pool.fetch(
        """
        SELECT m.conversation_id, m.role, m.content, m.created_at
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
        ORDER BY m.created_at DESC
        LIMIT 20
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

    (
        display,
        proposed,
        reasoning,
        proposed_override,
        proposed_section,
    ) = await coach_service.coach_reply(
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
        sections=inf.get("system_instructions_sections"),
    )

    # Coach Bucket 2 — coach_reply returns EXACTLY ONE of proposed (text)
    # / proposed_override (dict) / proposed_section (dict), or all three
    # None for plain-text turns. Persist whichever is present; add_message
    # supersedes any older pending proposals in the same conversation
    # inside its own transaction (Rishi 2026-06-11 follow-up).
    #
    # 2026-06-12 follow-up (per PR #361 contract refinement audit):
    # defensive snapshot injection. The META_PROMPT asks Coach to emit
    # section_heading + section_editable alongside section_id, but LLMs
    # forget fields. When Coach omits a snapshot, look up the live
    # section by section_id and inject the missing field so mobile's
    # badge render NEVER sees an incomplete blob. The live row stays
    # authoritative at apply time (which resolves via section_id);
    # the snapshots are purely a render-time convenience for mobile.
    if proposed_section:
        proposed_section = _ensure_section_snapshots(proposed_section, inf)

    coach_msg = await coach_repo.add_message(
        pool,
        coach_conversation_id,
        "coach",
        display,
        proposed_changes=proposed,
        reasoning=reasoning,
        proposed_global_rule_override=proposed_override,
        proposed_section_change=proposed_section,
    )
    await coach_repo.touch_session(pool, coach_conversation_id)

    # 2026-06-11 PR-4 (plan §4 item D): mobile uses this bool to gate
    # the Save button — disabled when no proposal is pending so the
    # "tap Save → mystery LLM round-trip" failure mode goes away.
    # Compute against the latest state (post-add_message) so a proposal
    # just emitted this turn shows pending_proposal_exists=true.
    pending = await coach_repo.pending_proposal(pool, coach_conversation_id)
    return {
        "creator_message": _format_message(creator_msg),
        "coach_message": _format_message(coach_msg),
        "pending_proposal_exists": pending is not None,
    }


@router.post("/conversations/{coach_conversation_id}/apply")
async def apply_coach_proposal(
    coach_conversation_id: str, body: dict, request: Request
):
    """Apply a SPECIFIC coach proposal (by id) to the bot.

    Coach PR-3 (migration 035): the request now carries `proposal_id`
    in the body — was implicit "whatever is most recent" pre-PR-3,
    which silently applied newer proposals when the creator scrolled
    up + tapped Save on an older card.

    Lifecycle:
      - proposal_id MUST exist + belong to this session + be a real
        proposal (not a receipt/creator msg/opening) → 404 otherwise.
      - status MUST be 'pending' → 409 with current status otherwise.
      - On success: transactionally mark other pending in this session
        as 'superseded', mark the chosen one as 'applied', then write
        the bot-side change (system_instructions or override merge).
        After return, the session has exactly 1 'applied' + 0 'pending'.
    """
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, coach_conversation_id)

    proposal_id = (body or {}).get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise HTTPException(
            status_code=422,
            detail="proposal_id is required (the coach_message id of the "
            "proposal card you tapped Save on)",
        )

    proposal = await coach_repo.get_proposal_by_id(
        pool, coach_conversation_id, proposal_id.strip()
    )
    if not proposal:
        raise HTTPException(
            status_code=404,
            detail="No such proposal in this session — id mismatch or wrong session",
        )
    current_status = proposal.get("status") or "na"
    if current_status != "pending":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "proposal_not_pending",
                "current_status": current_status,
                "message": f"Proposal status is {current_status!r}, not pending. "
                f"Newer proposals in this session are already applied or pending.",
            },
        )

    inf = await influencer_repo.get_by_id(pool, session["bot_id"])
    if not inf:
        raise HTTPException(status_code=410, detail="Underlying bot was deleted")

    # Coach Bucket 2 PR-2 — dispatch on proposal type. EXACTLY ONE column
    # is populated per proposal turn (enforced by coach_reply + repo
    # contracts). The three lanes:
    #   1. proposed_section_change → UPDATE one section body inside
    #      ai_influencers.system_instructions_sections via jsonb_set;
    #      validates previous_body_sha256 vs live body (409 stale_proposal
    #      on drift). [Bucket 2 PR-2]
    #   2. proposed_global_rule_override → JSONB merge into
    #      ai_influencers.global_rule_overrides. [Coach Fix 1 PR-B]
    #   3. proposed_changes (text) → UPDATE ai_influencers.system_instructions.
    #      [Historical Coach path]
    section_raw = proposal.get("proposed_section_change")
    if section_raw:
        import json as _json

        if isinstance(section_raw, str):
            try:
                section_blob = _json.loads(section_raw)
            except (_json.JSONDecodeError, TypeError):
                raise HTTPException(
                    status_code=500,
                    detail="Stored section_change blob is malformed; cannot apply",
                ) from None
        else:
            section_blob = section_raw
        if not isinstance(section_blob, dict):
            raise HTTPException(
                status_code=500,
                detail="Stored section_change blob is not a dict; cannot apply",
            )
        section_id = section_blob.get("section_id")
        new_body = section_blob.get("new_body")
        claimed_sha = section_blob.get("previous_body_sha256")
        if not (isinstance(section_id, str) and section_id.strip()):
            raise HTTPException(
                status_code=500,
                detail="Stored section_change blob missing section_id",
            )
        if not (isinstance(new_body, str) and new_body.strip()):
            raise HTTPException(
                status_code=500,
                detail="Stored section_change blob missing new_body",
            )

        # Pull the live sections array + locate the target section by id.
        live_sections_raw = inf.get("system_instructions_sections")
        if isinstance(live_sections_raw, str):
            try:
                live_sections = _json.loads(live_sections_raw)
            except (_json.JSONDecodeError, TypeError):
                live_sections = []
        else:
            live_sections = live_sections_raw or []
        if not isinstance(live_sections, list):
            live_sections = []
        target_index = None
        target_section = None
        for idx, sec in enumerate(live_sections):
            if isinstance(sec, dict) and sec.get("id") == section_id:
                target_index = idx
                target_section = sec
                break
        if target_index is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "section_not_found",
                    "section_id": section_id,
                    "message": (
                        f"Section {section_id!r} no longer exists on this bot. "
                        "The creator may have deleted it on the Soul File page."
                    ),
                },
            )
        if target_section.get("editable") is False:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "section_not_editable",
                    "section_id": section_id,
                    "message": (
                        f"Section {section_id!r} is read-only. The Soul File "
                        "page may have flipped editable=false since the proposal."
                    ),
                },
            )

        # Optimistic-concurrency check: the live body's sha must equal what
        # Coach claimed at proposal time. If drifted (creator edited the
        # section between proposal + apply, OR a parallel Coach turn
        # superseded it), refuse with 409 stale_proposal — same shape as
        # the section_not_editable branch so mobile can surface either
        # case via one error path.
        from services.coach import section_body_sha256 as _sha

        live_body = target_section.get("body") or ""
        live_sha = _sha(live_body)
        if isinstance(claimed_sha, str) and claimed_sha and claimed_sha != live_sha:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "stale_proposal",
                    "section_id": section_id,
                    "live_sha256": live_sha,
                    "claimed_sha256": claimed_sha,
                    "message": (
                        f"Section {section_id!r} body changed since Coach read it. "
                        "Re-open the Soul File page and have Coach propose again."
                    ),
                },
            )

        # Build the new section list + apply. Preserves all fields except
        # `body`; mobile-edited heading/editable on the same section stay.
        new_sections = list(live_sections)
        merged_section = dict(target_section)
        merged_section["body"] = new_body
        new_sections[target_index] = merged_section

        previous_text = f"section[{section_id}].body={live_body}"
        new_text = f"section[{section_id}].body={new_body}"
        history_row = await coach_repo.record_application(
            pool,
            bot_id=session["bot_id"],
            coach_conversation_id=coach_conversation_id,
            coach_message_id=str(proposal["id"]),
            previous_instructions=previous_text,
            new_instructions=new_text,
            applied_by=user_id,
        )
        await pool.execute(
            """
            UPDATE ai_influencers
            SET system_instructions_sections = $1::jsonb,
                updated_at = NOW()
            WHERE id = $2
            """,
            _json.dumps(new_sections),
            session["bot_id"],
        )
        await coach_repo.supersede_and_apply(
            pool, coach_conversation_id, str(proposal["id"])
        )
        receipt_content = (
            f"✅ Saved — {inf.get('display_name') or 'your bot'}'s "
            f"'{target_section.get('heading') or section_id}' section updated: "
            f"{proposal['content']}"
        )
        receipt_msg = await coach_repo.add_message(
            pool,
            coach_conversation_id,
            "coach",
            receipt_content,
        )
        return {
            "applied": True,
            "applied_type": "section_change",
            "section_id": section_id,
            "history_id": str(history_row["id"]),
            "applied_at": history_row["applied_at"].isoformat()
            if isinstance(history_row["applied_at"], datetime)
            else history_row["applied_at"],
            "receipt_message": _format_message(receipt_msg),
        }

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
        #
        # asyncpg returns JSONB columns as raw strings unless a JSON
        # codec is registered on the pool (which V2 doesn't do — see
        # app/database.py). For bots whose global_rule_overrides has
        # been touched before, inf.get("global_rule_overrides") is a
        # `str` not a `dict`. `{**str}` crashes with TypeError, surfaces
        # as 500 — bug found 2026-06-12 ~15:53 IST applying an override
        # on Anastasia (whose overrides column was already populated by
        # a prior apply). soul_file.py:_render_global_rules already does
        # this defensive parse for the same reason; mirror that here.
        existing_overrides = inf.get("global_rule_overrides") or {}
        if isinstance(existing_overrides, str):
            try:
                existing_overrides = _json.loads(existing_overrides) or {}
            except (_json.JSONDecodeError, TypeError):
                logger.warning(
                    "creator_coach: bot=%s has malformed global_rule_overrides JSONB; "
                    "treating as empty for apply",
                    session["bot_id"],
                )
                existing_overrides = {}
        if not isinstance(existing_overrides, dict):
            existing_overrides = {}
        prev_overrides_json = _json.dumps(existing_overrides)
        new_overrides_json = _json.dumps({**existing_overrides, key: value})
        # 2026-06-12: mobile expert reported override-apply still 500s
        # after the JSONB-string-decode fix in PR #370. The crash is in
        # ONE of the four DB calls below (record_application,
        # supersede_and_apply, add_message) but the response body was
        # a generic 500 with no JSON detail — i.e. uncaught Python
        # exception that bypassed our HTTPException(500, detail=...)
        # raises. Wrapping the suspect block in try/except so:
        #   1. The next 500 surfaces a clear error body naming WHICH
        #      step failed + the exception class
        #   2. Sentry capture fires with full traceback (the existing
        #      Sentry SDK is already configured in main.py)
        # This is diagnostic scaffolding — once the root cause is
        # known + fixed in a follow-up, the wrapper can stay (it's a
        # robust pattern) or come off (caller's choice).
        try:
            history_row = await coach_repo.record_application(
                pool,
                bot_id=session["bot_id"],
                coach_conversation_id=coach_conversation_id,
                coach_message_id=str(proposal["id"]),
                previous_instructions=f"global_rule_overrides={prev_overrides_json}",
                new_instructions=f"global_rule_overrides={new_overrides_json}",
                applied_by=user_id,
            )
        except Exception as e:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
            logger.exception(
                "coach.apply: record_application failed for override "
                "proposal=%s bot=%s session=%s",
                str(proposal["id"]),
                session["bot_id"],
                coach_conversation_id,
            )
            raise HTTPException(
                status_code=500,
                detail=f"override apply failed at record_application: "
                f"{type(e).__name__}: {e}",
            ) from e

        # Coach PR-3: typed lifecycle transition. Supersede every other
        # pending proposal in this session + mark this one applied.
        try:
            await coach_repo.supersede_and_apply(
                pool, coach_conversation_id, str(proposal["id"])
            )
        except Exception as e:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
            logger.exception(
                "coach.apply: supersede_and_apply failed for override "
                "proposal=%s session=%s",
                str(proposal["id"]),
                coach_conversation_id,
            )
            raise HTTPException(
                status_code=500,
                detail=f"override apply failed at supersede_and_apply: "
                f"{type(e).__name__}: {e}",
            ) from e

        receipt_content = (
            f"✅ Saved — platform-rule override applied for "
            f"{inf.get('display_name') or 'your bot'}: "
            f"{key} = {value}. {proposal['content']}"
        )
        try:
            receipt_msg = await coach_repo.add_message(
                pool,
                coach_conversation_id,
                "coach",
                receipt_content,
            )
        except Exception as e:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
            logger.exception(
                "coach.apply: receipt add_message failed for override "
                "proposal=%s session=%s receipt_content_len=%d",
                str(proposal["id"]),
                coach_conversation_id,
                len(receipt_content),
            )
            raise HTTPException(
                status_code=500,
                detail=f"override apply failed at receipt add_message: "
                f"{type(e).__name__}: {e}",
            ) from e
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
    # Coach PR-3: typed lifecycle transition. Supersede every other
    # pending proposal in this session + mark this one applied.
    await coach_repo.supersede_and_apply(
        pool, coach_conversation_id, str(proposal["id"])
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


@router.post("/conversations/{coach_conversation_id}/discard")
async def discard_coach_proposal(
    coach_conversation_id: str, body: dict, request: Request
):
    """Mark a specific proposal as discarded.

    Coach PR-3 — the explicit counterpart to /apply. Does NOT touch
    other pending proposals in this session (the creator may want to
    apply a different one). Idempotent: re-call on an already-discarded
    id is a 200 with `discarded: false` (nothing changed).

    Lifecycle checks mirror /apply exactly so a client can use the
    same error handling for both flows."""
    user_id = get_current_user(request)
    pool = await get_pool()
    await _load_owned_session(pool, user_id, coach_conversation_id)

    proposal_id = (body or {}).get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise HTTPException(
            status_code=422,
            detail="proposal_id is required",
        )

    proposal = await coach_repo.get_proposal_by_id(
        pool, coach_conversation_id, proposal_id.strip()
    )
    if not proposal:
        raise HTTPException(
            status_code=404,
            detail="No such proposal in this session — id mismatch or wrong session",
        )
    current_status = proposal.get("status") or "na"
    if current_status not in ("pending", "discarded"):
        # discarded is allowed for idempotency; pending is the actual
        # transition. Other statuses (applied / superseded / na) are
        # not legal targets for /discard.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "proposal_not_discardable",
                "current_status": current_status,
                "message": f"Proposal status is {current_status!r}; "
                f"only pending proposals can be discarded.",
            },
        )

    await coach_repo.mark_discarded(pool, str(proposal["id"]))
    return {
        "discarded": current_status == "pending",  # false if already discarded
        "proposal_id": str(proposal["id"]),
        "previous_status": current_status,
        "current_status": "discarded",
    }


@router.get("/conversations/{coach_conversation_id}/messages")
async def list_coach_messages(coach_conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()
    await _load_owned_session(pool, user_id, coach_conversation_id)
    messages = await coach_repo.list_messages(pool, coach_conversation_id)
    # 2026-06-11 PR-4 (plan §4 item D): mobile gates the Save button on
    # this bool. Computing here means a session-reload after navigating
    # away + back shows the correct Save-button state without needing
    # mobile to scan every coach_message for a proposal that wasn't
    # applied yet.
    pending = await coach_repo.pending_proposal(pool, coach_conversation_id)
    return {
        "coach_conversation_id": coach_conversation_id,
        "messages": [_format_message(m) for m in messages],
        "total": len(messages),
        "pending_proposal_exists": pending is not None,
    }
