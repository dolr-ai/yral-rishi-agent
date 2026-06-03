"""Phase 23.5 — skill state endpoints.

Three endpoints, all scoped to (current_user, influencer_id):
  GET    /api/v1/skills/{influencer_id}/state        — current state row
  POST   /api/v1/skills/{influencer_id}/state        — manual upsert (test/admin)
  PATCH  /api/v1/skills/{influencer_id}/preferences  — pause / resume / cadence

Onboarding-driven writes happen automatically from chat.py via the
<skill_state> hidden block (see services/skill_parser.py). These
endpoints exist so:
  - Rishi can poke state from the dashboard during V1 dogfooding
  - Mobile can later expose pause/resume + cadence edit UI without
    requiring chat round-trips

The influencer must have a skill_slug; otherwise routes return 404.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import influencer_repo, skill_state_repo
from services import skills as skills_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/skills", tags=["Skills"])


def _format(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "influencer_id": row["influencer_id"],
        "skill_slug": row["skill_slug"],
        "state": row.get("state") or {},
        "status": row.get("status"),
        "next_event_at": (
            row["next_event_at"].isoformat() if row.get("next_event_at") else None
        ),
        "last_event_at": (
            row["last_event_at"].isoformat() if row.get("last_event_at") else None
        ),
        "updated_at": (
            row["updated_at"].isoformat() if row.get("updated_at") else None
        ),
    }


async def _require_skilled_influencer(pool, influencer_id: str) -> dict:
    """Fetch the influencer + verify it has a skill_slug. 404 otherwise."""
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if not inf.get("skill_slug"):
        raise HTTPException(status_code=404, detail="Influencer has no skill assigned")
    if not skills_catalog.get(inf["skill_slug"]):
        # Influencer points to a slug that no longer exists in the catalog
        # — treat as 404 rather than 500 because the catalog is the source
        # of truth, not the influencer row.
        raise HTTPException(status_code=404, detail="Skill not found in catalog")
    return inf


@router.get("/{influencer_id}/state")
async def get_state(influencer_id: str, request: Request):
    """Return the calling user's skill state for this influencer, or null
    if onboarding hasn't run yet."""
    user_id = get_current_user(request)
    pool = await get_pool()
    await _require_skilled_influencer(pool, influencer_id)
    row = await skill_state_repo.get(pool, user_id, influencer_id)
    return {"state": _format(row)}


@router.post("/{influencer_id}/state")
async def upsert_state(influencer_id: str, request: Request):
    """Manual state write. V1 surface for testing — the production write
    path is the <skill_state> block from chat.py. Body shape:
        {"state": {"setup": {...}, "runtime": {...}}, "status": "active"}
    Missing keys default sensibly. Always uses the influencer's catalog
    slug — caller doesn't choose the skill."""
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _require_skilled_influencer(pool, influencer_id)
    body = await request.json()
    new_state = body.get("state") or {}
    if not isinstance(new_state, dict):
        raise HTTPException(status_code=400, detail="state must be an object")
    status = body.get("status") or "active"
    if status not in ("active", "paused", "done", "onboarding_partial"):
        raise HTTPException(status_code=400, detail=f"invalid status {status!r}")
    row = await skill_state_repo.upsert(
        pool,
        user_id=user_id,
        influencer_id=influencer_id,
        skill_slug=inf["skill_slug"],
        state=new_state,
        status=status,
    )
    return {"state": _format(row)}


@router.patch("/{influencer_id}/preferences")
async def patch_preferences(influencer_id: str, request: Request):
    """Pause / resume the skill, or merge preference updates into
    state.setup. Body keys (all optional):
        {
          "status": "paused" | "active",
          "preferences": {<merged into state.setup>}
        }
    """
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _require_skilled_influencer(pool, influencer_id)
    body = await request.json()

    existing = await skill_state_repo.get(pool, user_id, influencer_id)
    if not existing:
        raise HTTPException(
            status_code=404, detail="No skill state to update — onboard first"
        )

    new_status = body.get("status")
    if new_status:
        if new_status == "paused":
            await skill_state_repo.pause(
                pool, user_id=user_id, influencer_id=influencer_id
            )
        elif new_status == "active":
            await skill_state_repo.resume(
                pool, user_id=user_id, influencer_id=influencer_id
            )
        else:
            raise HTTPException(
                status_code=400, detail="status must be paused or active"
            )

    prefs = body.get("preferences")
    if prefs is not None:
        if not isinstance(prefs, dict):
            raise HTTPException(status_code=400, detail="preferences must be an object")
        # Merge into setup half, leave runtime untouched. The upsert SQL
        # does `state || EXCLUDED.state` so passing {"setup": new_prefs}
        # merges keys at the top level — the existing runtime sub-dict
        # is preserved because we don't overwrite it here.
        merged_setup = {**(existing["state"].get("setup") or {}), **prefs}
        await skill_state_repo.upsert(
            pool,
            user_id=user_id,
            influencer_id=influencer_id,
            skill_slug=inf["skill_slug"],
            state={"setup": merged_setup},
            status=new_status or existing.get("status") or "active",
        )

    row = await skill_state_repo.get(pool, user_id, influencer_id)
    return {"state": _format(row)}
