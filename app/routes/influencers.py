import json
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query, Header
from fastapi.responses import JSONResponse

import config
from database import get_pool
from auth import get_current_user
from repositories import influencer_repo, video_idea_repo
from services import (
    moderation,
    character_generator,
    google_chat,
    video_ideas as video_ideas_service,
    influencer_summary,
    surface as surface_service,
)
from services.character_generator import GeminiSafetyBlocked
from models import (
    CreateInfluencerRequest,
    GeneratePromptRequest,
    GeneratePromptResponse,
    ValidateAndGenerateRequest,
    ValidateAndGenerateResponse,
    UpdateSystemPromptRequest,
    GenerateVideoPromptRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Influencers"])


def _format_influencer_response(inf: dict) -> dict:
    system_instructions = inf.get("system_instructions", "")
    system_prompt_display = (
        moderation.strip_guardrails(system_instructions) if system_instructions else ""
    )

    return {
        "id": inf["id"],
        "name": inf["name"],
        "display_name": inf["display_name"],
        "avatar_url": inf.get("avatar_url") or "",
        "description": inf.get("description") or "",
        "category": inf.get("category") or "",
        "is_active": inf.get("is_active", "active"),
        # Defaulted here as well as in the DB so callers that build a dict
        # without the column (tests, the trending/search queries that don't
        # select it yet) still get a valid surface rather than a null the
        # web client has to special-case.
        "surface": inf.get("surface") or surface_service.MOBILE,
        "parent_principal_id": inf.get("parent_principal_id"),
        "source": inf.get("source"),
        "system_prompt": system_prompt_display,
        "created_at": inf["created_at"].isoformat()
        if isinstance(inf["created_at"], datetime)
        else str(inf["created_at"]),
        "conversation_count": inf.get("conversation_count"),
        "message_count": inf.get("message_count"),
    }


def _format_influencer_detail(inf: dict) -> dict:
    personality_traits = inf.get("personality_traits")
    if isinstance(personality_traits, str):
        try:
            personality_traits = json.loads(personality_traits)
        except (json.JSONDecodeError, TypeError):
            personality_traits = {}

    suggested_messages = inf.get("suggested_messages")
    if isinstance(suggested_messages, str):
        try:
            suggested_messages = json.loads(suggested_messages)
        except (json.JSONDecodeError, TypeError):
            suggested_messages = []

    metadata = inf.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    system_instructions = inf.get("system_instructions", "")
    system_instructions_display = moderation.strip_guardrails(system_instructions)

    return {
        "id": inf["id"],
        "name": inf["name"],
        "display_name": inf["display_name"],
        "avatar_url": inf.get("avatar_url"),
        "description": inf.get("description"),
        "category": inf.get("category"),
        "system_instructions": system_instructions_display,
        "personality_traits": personality_traits,
        "initial_greeting": inf.get("initial_greeting"),
        "suggested_messages": suggested_messages,
        "is_active": inf.get("is_active", "active"),
        "is_nsfw": inf.get("is_nsfw", False),
        "parent_principal_id": inf.get("parent_principal_id"),
        "source": inf.get("source"),
        "created_at": inf["created_at"].isoformat()
        if isinstance(inf["created_at"], datetime)
        else str(inf["created_at"]),
        "updated_at": inf["updated_at"].isoformat()
        if isinstance(inf["updated_at"], datetime)
        else str(inf["updated_at"]),
        "metadata": metadata,
        "conversation_count": inf.get("conversation_count"),
    }


@router.get("/influencers")
async def list_influencers(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    surface: str | None = Query(
        default=None,
        description="Filter to a product surface: mobile | web | both. "
        "Omit for the unfiltered catalogue (existing behaviour).",
    ),
):
    # Reject an unknown surface rather than falling back to unfiltered. A
    # typo'd ?surface=wbe from amorae-web must NOT quietly return the whole
    # mainstream catalogue to the adult site — the one failure this filter
    # exists to prevent.
    if surface is not None and surface_service.normalize(surface) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid surface '{surface}'. Expected one of: "
            f"{', '.join(surface_service.VALID_SURFACES)}",
        )
    surfaces = surface_service.visible_surfaces(surface)

    try:
        pool = await get_pool()
        influencers = await influencer_repo.list_all(pool, limit, offset, surfaces)
        total = await influencer_repo.count_all(pool, surfaces)

        response = JSONResponse(
            content={
                "influencers": [_format_influencer_response(i) for i in influencers],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    except Exception as e:
        logger.error(f"list_influencers failed: {type(e).__name__}: {e}")
        import sentry_sdk

        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=500, detail=f"Internal error: {type(e).__name__}: {e}"
        )


_TRENDING_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_TRENDING_CACHE_TTL_SEC = 60.0


@router.get("/influencers/trending")
async def list_trending(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List trending influencers, served from a tiny per-replica TTL cache.

    Task A: occasional 5-30s tail-latency on this endpoint (seen across
    every batch's 27/27 suite run) traces to edge/network variance, not the
    DB query itself (10-37ms server-side per direct asyncpg bench). The
    materialized view already refreshes via REFRESH ... CONCURRENTLY and
    doesn't block reads.

    A 60s process-local cache eliminates the DB+network round-trip for the
    common (limit, offset) shapes that drive most traffic. Per-replica
    cache (no Redis) keeps the fix tiny — worst case each replica
    recomputes once per minute. The materialized view refresh is on a
    15-min cycle, so 60s freshness is fine for "trending."
    """
    import time

    key = (limit, offset)
    cached = _TRENDING_CACHE.get(key)
    now = time.monotonic()
    if cached and (now - cached[0]) < _TRENDING_CACHE_TTL_SEC:
        response = JSONResponse(content=cached[1])
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["X-Cache"] = "HIT"
        return response

    pool = await get_pool()
    influencers = await influencer_repo.list_trending(pool, limit, offset)
    total = await influencer_repo.count_trending(pool)

    formatted = []
    for i in influencers:
        inf = _format_influencer_response(i)
        inf["message_count"] = i.get("message_count", 0)
        inf["conversation_count"] = i.get("conversation_count", 0)
        formatted.append(inf)

    payload = {
        "influencers": formatted,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    _TRENDING_CACHE[key] = (now, payload)

    response = JSONResponse(content=payload)
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Cache"] = "MISS"
    return response


@router.get("/influencers/{influencer_id}")
async def get_influencer(influencer_id: str):
    pool = await get_pool()
    inf = await influencer_repo.get_with_conversation_count(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    response = JSONResponse(content=_format_influencer_detail(inf))
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@router.get("/influencers/{influencer_id}/summary")
async def get_influencer_summary(influencer_id: str):
    """Coach Fix 2 backend — plain-English bullet summary of what the
    bot does. 5-7 bullets covering personality + reply behavior, each
    optionally tagged with an `override_target` slug pointing at one
    of the overrideable GLOBAL_RULES so mobile can offer "tap to
    override" CTAs.

    Cached on the bot row (`metadata.plain_english_summary`) keyed by
    the bot's `updated_at` for staleness; regenerated on the next call
    after any edit to the bot. Public — no auth — same as the
    /influencers/{id} detail endpoint above."""
    pool = await get_pool()
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    cached = influencer_summary.cache_is_fresh(inf)
    if cached is not None:
        response = JSONResponse(content=cached)
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["X-Summary-Cache"] = "hit"
        return response

    try:
        summary = await influencer_summary.generate_for_influencer(inf)
    except Exception as e:
        logger.exception(
            "summary generation failed for influencer %s: %s", influencer_id, e
        )
        raise HTTPException(
            status_code=503, detail="summary unavailable — try again shortly"
        ) from None

    await influencer_repo.cache_plain_english_summary(pool, influencer_id, summary)

    response = JSONResponse(content=summary)
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Summary-Cache"] = "miss"
    return response


@router.post(
    "/influencers/generate-prompt", response_model=GeneratePromptResponse
)
async def generate_prompt(body: GeneratePromptRequest, request: Request):
    get_current_user(request)
    try:
        instructions = await character_generator.generate_system_instructions(
            body.concept
        )
    except GeminiSafetyBlocked as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not instructions:
        raise HTTPException(
            status_code=500, detail="Failed to generate system instructions"
        )
    return {"system_instructions": instructions}


@router.post(
    "/influencers/validate-and-generate-metadata",
    response_model=ValidateAndGenerateResponse,
)
async def validate_and_generate(body: ValidateAndGenerateRequest, request: Request):
    get_current_user(request)
    result = await character_generator.validate_and_generate_metadata(body.concept)
    if not result:
        raise HTTPException(
            status_code=500, detail="Failed to validate and generate metadata"
        )
    # avatar_url is None when avatar generation fails server-side; the
    # response model pins it as a plain string (anyOf-null schemas get
    # dropped by some codegen clients — apple/swift-openapi-generator#817),
    # so coalesce to "" — the clients already treat blank as "no avatar".
    result["avatar_url"] = result.get("avatar_url") or ""
    return result


@router.post(
    "/influencers/create",
    status_code=201,
    responses={409: {"description": "Name is already taken"}},
)
async def create_influencer(body: CreateInfluencerRequest, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    existing = await influencer_repo.get_by_name(pool, body.name)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Name '{body.name}' is already taken"
        )

    safe_instructions = moderation.with_guardrails(body.system_instructions)

    greeting = body.initial_greeting
    suggestions = body.suggested_messages
    if not greeting or not suggestions:
        (
            gen_greeting,
            gen_suggestions,
        ) = await character_generator.generate_initial_greeting(
            body.display_name,
            body.system_instructions,
        )
        if not greeting:
            greeting = gen_greeting
        if not suggestions:
            suggestions = gen_suggestions

    influencer_data = {
        "id": body.bot_principal_id,
        "name": body.name,
        "display_name": body.display_name,
        "avatar_url": body.avatar_url,
        "description": body.description,
        "category": body.category,
        "system_instructions": safe_instructions,
        "personality_traits": body.personality_traits,
        "initial_greeting": greeting,
        "suggested_messages": suggestions,
        "is_active": "active",
        "is_nsfw": False,
        "parent_principal_id": user_id,
        "source": body.source or "user_created",
        "metadata": body.metadata,
    }

    created = await influencer_repo.create(pool, influencer_data)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create influencer")

    starter_video_prompt = await character_generator.generate_video_prompt(
        body.display_name,
        body.system_instructions,
    )

    response = _format_influencer_detail(created)
    response["starter_video_prompt"] = starter_video_prompt
    return response


@router.patch("/influencers/{influencer_id}/system-prompt")
async def update_system_prompt(
    influencer_id: str,
    body: UpdateSystemPromptRequest,
    request: Request,
):
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Only the creator can update this influencer"
        )

    safe_instructions = moderation.with_guardrails(body.system_instructions)
    await influencer_repo.update_system_prompt(pool, influencer_id, safe_instructions)

    updated = await influencer_repo.get_with_conversation_count(pool, influencer_id)
    return _format_influencer_detail(updated)


@router.post("/influencers/{influencer_id}/generate-video-prompt")
async def generate_video_prompt_endpoint(
    influencer_id: str,
    body: GenerateVideoPromptRequest,
    request: Request,
):
    get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    prompt = await character_generator.generate_video_prompt(
        inf["display_name"],
        inf["system_instructions"],
    )
    if not prompt:
        raise HTTPException(status_code=500, detail="Failed to generate video prompt")
    return {"prompt": prompt}


@router.delete("/influencers/{influencer_id}")
async def delete_influencer(influencer_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Only the creator can delete this influencer"
        )

    await influencer_repo.soft_delete(pool, influencer_id)
    deleted = await influencer_repo.get_by_id(pool, influencer_id)
    return _format_influencer_detail(deleted)


@router.post("/admin/influencers/{influencer_id}")
async def admin_ban(
    influencer_id: str,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    if (
        not config.ADMIN_KEY
        or not x_admin_key
        or not secrets.compare_digest(x_admin_key, config.ADMIN_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")

    pool = await get_pool()
    inf = await influencer_repo.get_by_id_or_name(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    try:
        await influencer_repo.ban(pool, inf["id"])
        updated = await influencer_repo.get_by_id(pool, inf["id"])
        await google_chat.notify_influencer_banned(
            inf["id"], inf.get("display_name", "Unknown")
        )
        return _format_influencer_detail(updated)
    except Exception as e:
        await google_chat.notify_influencer_ban_failed(inf["id"], str(e))
        raise HTTPException(status_code=500, detail=f"Ban failed: {e}")


@router.post("/admin/influencers/{influencer_id}/unban")
async def admin_unban(
    influencer_id: str,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    if (
        not config.ADMIN_KEY
        or not x_admin_key
        or not secrets.compare_digest(x_admin_key, config.ADMIN_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")

    pool = await get_pool()
    inf = await influencer_repo.get_by_id_or_name(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    try:
        await influencer_repo.unban(pool, inf["id"])
        updated = await influencer_repo.get_by_id(pool, inf["id"])
        await google_chat.notify_influencer_unbanned(
            inf["id"], inf.get("display_name", "Unknown")
        )
        return _format_influencer_detail(updated)
    except Exception as e:
        await google_chat.notify_influencer_unban_failed(inf["id"], str(e))
        raise HTTPException(status_code=500, detail=f"Unban failed: {e}")


# ───────────────── Phase 22.3 — video ideas (owner-only) ──────────────────


def _format_video_idea(row: dict) -> dict:
    """Mobile renders chips from this shape. batch_date is ISO so mobile
    can group ideas by day; used_at is ISO-or-null."""
    return {
        "id": str(row["id"]),
        "influencer_id": row["influencer_id"],
        "batch_date": (
            row["batch_date"].isoformat()
            if hasattr(row["batch_date"], "isoformat")
            else str(row["batch_date"])
        ),
        "rank": row["rank"],
        "hook": row["hook"],
        "idea_text": row["idea_text"],
        "status": row["status"],
        "used_at": (
            row["used_at"].isoformat()
            if row.get("used_at") and isinstance(row["used_at"], datetime)
            else None
        ),
        "created_at": (
            row["created_at"].isoformat()
            if isinstance(row["created_at"], datetime)
            else row["created_at"]
        ),
    }


@router.get("/influencers/{influencer_id}/video-ideas")
async def list_video_ideas(influencer_id: str, request: Request):
    """Latest batch of ~5 video idea chips for an owned influencer.

    Cold-start path: if no batch has ever been written for this bot,
    we generate one ON DEMAND so the creator's first session isn't
    blank. ~3-5s LLM latency is acceptable here because mobile already
    shows a loading state on this screen. Subsequent calls hit the
    nightly cron's batch and return immediately."""
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Only the creator can see this influencer's ideas"
        )

    ideas = await video_idea_repo.latest_batch_for_bot(pool, influencer_id)
    if not ideas:
        # Cold-start — generate one batch synchronously. Any failure
        # falls through to an empty list; mobile renders "no ideas yet,
        # check back tomorrow." Subsequent cron passes will populate.
        try:
            await video_ideas_service.generate_for_one_bot(pool, dict(inf))
        except Exception:
            logger.exception(
                "video_ideas: cold-start gen failed for influencer %s",
                influencer_id,
            )
        ideas = await video_idea_repo.latest_batch_for_bot(pool, influencer_id)

    return {
        "influencer_id": influencer_id,
        "ideas": [_format_video_idea(r) for r in ideas],
        "total": len(ideas),
    }


@router.post("/influencers/{influencer_id}/video-ideas/{idea_id}/used")
async def mark_video_idea_used(influencer_id: str, idea_id: str, request: Request):
    """Mobile calls this when the creator taps Create on an idea chip.
    Flips status from 'fresh' to 'used' and stamps used_at. Idempotent:
    re-calling after status='used' returns the existing row unchanged."""
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Only the creator can mark ideas used"
        )

    row = await video_idea_repo.mark_used(pool, idea_id)
    if not row:
        raise HTTPException(status_code=404, detail="Idea not found")
    # Belt-and-suspenders: confirm the idea belongs to the claimed
    # influencer. Should always hold (URL bot_id matches the row), but
    # protects against a creator passing someone else's idea_id.
    if row["influencer_id"] != influencer_id:
        raise HTTPException(status_code=404, detail="Idea not found")

    return {"idea": _format_video_idea(row)}
