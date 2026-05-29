"""Phase 7.9 — 5-minute bot creation wizard endpoints.

Four steps:
  POST /api/v1/creator/wizard/start                — creator concept → questions
  POST /api/v1/creator/wizard/sessions/{id}/answer — answer one, get next or draft
  GET  /api/v1/creator/wizard/sessions/{id}/preview — sample 5-turn conversation
  POST /api/v1/creator/wizard/sessions/{id}/commit  — create the ai_influencers row

Session state is keyed on the authenticated creator's user_id; non-owners
get 403 (the answer/preview/commit endpoints verify session.creator_user_id).
"""

import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import influencer_repo, wizard_repo
from services import wizard as wizard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator/wizard", tags=["Bot Creation Wizard"])


async def _load_owned_session(pool, user_id: str, session_id: str) -> dict:
    session = await wizard_repo.get_session(pool, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Wizard session not found")
    if session["creator_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not your wizard session")
    return session


def _serialize_session(session: dict) -> dict:
    created_at = session.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": str(session["id"]),
        "creator_user_id": session["creator_user_id"],
        "concept": session["concept"],
        "questions": session.get("questions") or [],
        "answers": session.get("answers") or {},
        "draft": _draft_or_none(session),
        "committed_bot_id": session.get("committed_bot_id"),
        "created_at": created_at,
    }


def _draft_or_none(session: dict) -> dict | None:
    if not session.get("draft_system_instructions"):
        return None
    return {
        "system_instructions": session["draft_system_instructions"],
        "display_name": session.get("draft_display_name"),
        "category": session.get("draft_category"),
        "initial_greeting": session.get("draft_initial_greeting"),
    }


@router.post("/start", status_code=201)
async def start_wizard(body: dict, request: Request):
    """Start a wizard session. Creator gives a 1-2 sentence concept; wizard
    generates 3-5 tailored intake questions."""
    user_id = get_current_user(request)
    concept = (body or {}).get("concept")
    if not isinstance(concept, str) or not concept.strip():
        raise HTTPException(status_code=422, detail="concept is required")
    concept = concept.strip()[:500]

    pool = await get_pool()
    questions = await wizard_service.generate_intake_questions(concept)
    if not questions:
        # Fall back to a fixed minimal intake so the wizard isn't blocked when
        # Gemini is flaky. Mirrors the spec's "things the bot WOULD say vs
        # WOULDN'T say" structure.
        questions = [
            {
                "key": "archetype",
                "question": "Which archetype fits best — companion, advisor, entertainer, educator, or creator?",
                "rationale": "Drives prompt template + LLM tuning",
            },
            {
                "key": "backstory",
                "question": "Give 1-2 unique backstory details that make this bot distinctive (not generic).",
                "rationale": "Distinctive bots retain users",
            },
            {
                "key": "voice",
                "question": "Conversation style — formal, casual, Hinglish? Should the bot match the user's language?",
                "rationale": "Language match drives engagement",
            },
            {
                "key": "would_say",
                "question": "Give one concrete sentence the bot WOULD say.",
                "rationale": "Captures voice",
            },
            {
                "key": "wouldnt_say",
                "question": "Give one thing the bot WOULDN'T say (a hard rule).",
                "rationale": "Captures guardrails",
            },
        ]

    session = await wizard_repo.create_session(pool, user_id, concept)
    await wizard_repo.set_questions(pool, str(session["id"]), questions)
    session["questions"] = questions
    return _serialize_session(session)


@router.post("/sessions/{session_id}/answer")
async def answer_question(session_id: str, body: dict, request: Request):
    """Record one answer. When all questions are answered, generate + cache
    the Soul File draft so /preview and /commit can use it."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, session_id)
    if session.get("committed_bot_id"):
        raise HTTPException(status_code=409, detail="Wizard already committed this bot")

    key = (body or {}).get("key")
    value = (body or {}).get("value")
    if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=422, detail="key and value are required")
    key = key.strip()
    value = value.strip()

    valid_keys = {q["key"] for q in (session.get("questions") or [])}
    if key not in valid_keys:
        raise HTTPException(
            status_code=422, detail=f"key must be one of {sorted(valid_keys)}"
        )

    session = await wizard_repo.record_answer(pool, session_id, key, value)

    # All questions answered → generate the draft so /preview + /commit work
    remaining = [
        q
        for q in (session.get("questions") or [])
        if q["key"] not in session["answers"]
    ]
    if not remaining and not session.get("draft_system_instructions"):
        draft = await wizard_service.generate_draft(
            session["concept"], session["answers"]
        )
        if draft:
            await wizard_repo.save_draft(
                pool,
                session_id,
                draft["system_instructions"],
                draft["display_name"],
                draft["category"],
                draft["initial_greeting"],
            )
            session["draft_system_instructions"] = draft["system_instructions"]
            session["draft_display_name"] = draft["display_name"]
            session["draft_category"] = draft["category"]
            session["draft_initial_greeting"] = draft["initial_greeting"]

    return {
        "session": _serialize_session(session),
        "remaining_questions": remaining,
    }


@router.get("/sessions/{session_id}/preview")
async def preview_session(session_id: str, request: Request):
    """Synthesize a 5-turn conversation between the draft bot and a synthetic
    user. Lets the creator see real output before committing."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, session_id)
    if not session.get("draft_system_instructions"):
        raise HTTPException(
            status_code=409,
            detail="Draft isn't ready yet — answer all wizard questions first",
        )

    messages = await wizard_service.generate_preview(
        session["draft_display_name"] or "bot",
        session["draft_category"] or "companion",
        session["draft_system_instructions"],
    )
    return {
        "session_id": str(session["id"]),
        "draft": _draft_or_none(session),
        "messages": messages,
        "hint": None
        if messages
        else "Preview generation failed — try again or commit as-is",
    }


@router.post("/sessions/{session_id}/commit", status_code=201)
async def commit_session(session_id: str, request: Request):
    """Finalize the wizard — create the ai_influencers row from the draft.
    Marks the wizard session as committed (cannot edit further)."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, session_id)
    if session.get("committed_bot_id"):
        raise HTTPException(
            status_code=409, detail="Wizard session is already committed"
        )
    if not session.get("draft_system_instructions"):
        raise HTTPException(
            status_code=409,
            detail="Draft isn't ready — answer all wizard questions first",
        )

    bot_id = f"wizard-{secrets.token_hex(6)}"
    inf = await influencer_repo.create(
        pool,
        {
            "id": bot_id,
            "name": session["draft_display_name"].lower().replace(" ", "")[:30],
            "display_name": session["draft_display_name"],
            "system_instructions": session["draft_system_instructions"],
            "initial_greeting": session["draft_initial_greeting"],
            "parent_principal_id": user_id,
            "category": session["draft_category"],
            "is_active": "active",
        },
    )
    await wizard_repo.mark_committed(pool, session_id, bot_id)
    return {
        "session_id": str(session["id"]),
        "bot_id": bot_id,
        "display_name": inf.get("display_name"),
        "category": inf.get("category"),
    }
