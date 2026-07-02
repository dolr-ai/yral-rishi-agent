"""Spicy chat gate — context read endpoint (track 2b).

Contract: docs/amorae-v2-contract-2026-07-01.md §3
Design: docs/spicy-chat-gate-design-2026-06-28.md §4.2

Server-to-server GET from amorae-web when a user starts a new web
chat thread — pulls the last N SFW-side (user, Tara) messages so
the web brand's "Tara" can pick up mid-thought instead of starting
cold. Read-only. Adult replies NEVER flow back into v2 (that's the
Level-2 write isolation — amorae writes only to amorae_db).

Auth: X-Amorae-Secret (reused from track 1b's amorae_auth.py — no
new middleware, no reshuffle). Same server-to-server auth model as
the handoff exchange endpoint.

bot_handle resolution (Session 6 verdict, pre-2b clarification):
  SELECT id FROM ai_influencers
   WHERE name = $1 AND is_active = 'active' AND is_nsfw = TRUE
   ORDER BY created_at ASC LIMIT 1
Multiple "Tara" rows exist in the catalog; the is_nsfw filter picks
the amorae-facing one (`taaarraaah`) regardless of which "tara"
slug comes through the URL. If a future PR turns Tara ji NSFW, the
deterministic ORDER BY breaks the tie by creation order.

Unknown user / unknown bot / no conversation → 200 {"messages": []}
(never 404 — leaks user-existence + amorae doesn't have to
differentiate "user not found" from "no chat history yet").

SFW filter today: role IN ('user','assistant') AND content IS NOT NULL
AND content <> ''. Content-level SFW tightening lands with track 2c
(SFW-constrain + deflect at generation time, at which point we'll
add per-message is_nsfw_content or filter by generation timestamp).
"""

import logging

from fastapi import APIRouter, Depends, Query

from amorae_auth import require_amorae_secret
from database import get_pool
from repositories import conversation_repo, influencer_repo, message_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/spicy/context", tags=["Spicy — Context"])


# Amorae sends 20 per the contract; we cap at 50 so a runaway query
# param can't request the entire message table for one bot.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


@router.get("", dependencies=[Depends(require_amorae_secret)])
async def get_spicy_context(
    user_id: str = Query(..., min_length=1, max_length=255),
    bot_handle: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict:
    """Return {"messages": [{"role", "content"}, ...]} — oldest-first,
    capped at min(limit, 50). All "unknown / empty" outcomes return
    200 with an empty list."""
    pool = await get_pool()

    influencer_id = await influencer_repo.get_active_nsfw_id_by_name(pool, bot_handle)
    if influencer_id is None:
        # Bot doesn't exist / isn't NSFW / isn't active. Amorae's job
        # to notice this in logs; user gets a clean empty context.
        return {"messages": []}

    conv = await conversation_repo.get_existing(pool, user_id, influencer_id)
    if conv is None:
        return {"messages": []}

    messages = await message_repo.list_recent_for_spicy_context(pool, conv["id"], limit)
    return {"messages": messages}
