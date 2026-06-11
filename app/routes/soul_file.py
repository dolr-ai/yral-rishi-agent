"""Coach Bucket 2 PR-3 — Soul File page endpoints.

Owner-gated GET + PUT for the bot's `system_instructions_sections`
JSONB array. Mobile's Soul File page reads via GET, writes via PUT;
Coach proposals dispatch through /apply (creator_coach.py) instead.

Contract: docs/designs/coach-bucket-2-sections-contract.md §5.

Key shape concerns enforced here:

  * Owner-gate — same `parent_principal_id != user_id` check as
    /api/v1/influencers/{id}/system-prompt.
  * Optimistic concurrency — PUT takes
    `expected_sections_version_sha256`; mismatch returns 409
    stale_sections with the CURRENT state embedded so mobile can
    reconcile without a re-GET (per the #361 mobile-expert refinement).
  * Validation (422) — section ids must be lowercase snake_case slugs,
    unique within a bot, body strings non-empty. Cap at 8 sections
    per bot per contract §9.
  * Fallback — bots without sections (the today-state for the 3,941
    existing rows) return a single synthetic "Core personality" section
    wrapping their flat `system_instructions` so mobile renders the
    same UI shell pre- and post-cutover (#361 mobile-expert refinement).
"""

import hashlib
import json as _json
import logging
import re

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import influencer_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Soul File"])


# ─── shape helpers ────────────────────────────────────────────────────────


# Lowercase snake_case slug: starts with a letter, contains a-z/0-9/_,
# at least 2 chars, at most 64. Matches the typical "id" pattern in the
# rest of the codebase.
_SECTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# Per contract §9 #4 — cap to keep Coach META_PROMPT input-token budget
# manageable + UI ergonomics sane. If a creator legitimately needs >8
# sections we'll revisit; for now the cap is enforced server-side so a
# misbehaving client can't blow up the prompt.
_MAX_SECTIONS = 8

# Synthetic-section id + heading per contract refinement (#361). The
# fallback section must use these literal values so mobile renders the
# same UI shell whether the bot has real sections or flat-text fallback.
_FALLBACK_ID = "core_personality"
_FALLBACK_HEADING = "Core personality"


def _coerce_sections_list(raw) -> list[dict]:
    """asyncpg returns JSONB as either decoded list/dict or as a JSON
    string depending on codec config. Normalise to list[dict]; return
    [] on anything malformed. The endpoint surface deals with empty
    via the fallback path."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (_json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict)]


def _canonical_sections_sha256(sections: list[dict]) -> str:
    """sha256 of the canonical JSON encoding of the sections array.
    Used as the optimistic-concurrency handle on PUT. Canonical =
    sort_keys + no insignificant whitespace so byte-identical content
    produces a byte-identical sha regardless of how the input array
    was originally serialised (mobile, server-side compose, manual
    edit, etc.)."""
    canonical = _json.dumps(sections, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_sections_shape(sections) -> list[dict]:
    """Return normalised sections list or raise 422 on bad shape.
    Validation rules per contract §5 and §9 #4:

      * sections must be a list
      * len(sections) <= _MAX_SECTIONS
      * each entry is a dict
      * each entry has a string `id` matching _SECTION_ID_RE
      * ids are unique within the bot
      * each entry has a non-empty `body` string
      * `heading` defaults to a Title-Case of the id if missing
      * `editable` defaults to True if missing
      * unknown fields are silently dropped (forward-compat)
    """
    if not isinstance(sections, list):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "sections_not_a_list",
                "message": "Top-level `sections` must be a JSON array.",
            },
        )
    if len(sections) > _MAX_SECTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_many_sections",
                "max": _MAX_SECTIONS,
                "got": len(sections),
                "message": (
                    f"Soul File caps at {_MAX_SECTIONS} sections per bot. "
                    f"Got {len(sections)}. Merge sections before saving."
                ),
            },
        )
    seen_ids: set[str] = set()
    normalised: list[dict] = []
    for idx, raw_section in enumerate(sections):
        if not isinstance(raw_section, dict):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "section_not_an_object",
                    "index": idx,
                    "message": f"Section at index {idx} is not a JSON object.",
                },
            )
        section_id = raw_section.get("id")
        if not (isinstance(section_id, str) and _SECTION_ID_RE.match(section_id)):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "bad_section_id",
                    "index": idx,
                    "got": section_id,
                    "message": (
                        "Section id must be a lowercase snake_case slug "
                        "(a-z, digits, underscore), 2-64 chars, starting "
                        "with a letter."
                    ),
                },
            )
        if section_id in seen_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "duplicate_section_id",
                    "id": section_id,
                    "message": (
                        f"Section id {section_id!r} appears twice. Ids "
                        "must be unique within a bot."
                    ),
                },
            )
        seen_ids.add(section_id)
        body = raw_section.get("body")
        if not (isinstance(body, str) and body.strip()):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "empty_section_body",
                    "id": section_id,
                    "message": (
                        f"Section {section_id!r} body must be a non-empty "
                        "string. Use a placeholder if the creator hasn't "
                        "written it yet."
                    ),
                },
            )
        heading = raw_section.get("heading")
        if not isinstance(heading, str) or not heading.strip():
            heading = section_id.replace("_", " ").title()
        editable = raw_section.get("editable")
        if not isinstance(editable, bool):
            editable = True
        normalised.append(
            {
                "id": section_id,
                "heading": heading.strip(),
                "body": body,
                "editable": editable,
            }
        )
    return normalised


def _build_fallback_section(flat_text: str) -> dict:
    """Wrap the bot's flat `system_instructions` in a single synthetic
    section so the GET response shape is constant whether the bot has
    real sections or not (mobile renders the same UI shell either way,
    per #361 refinement). The fallback section uses the fixed id +
    heading specified in the contract; editable=true means a creator
    who edits + PUTs converts the bot from flat-text to sectioned mode
    in one step."""
    return {
        "id": _FALLBACK_ID,
        "heading": _FALLBACK_HEADING,
        "body": flat_text or "",
        "editable": True,
    }


async def _load_owned_influencer(pool, user_id: str, bot_id: str) -> dict:
    """Shared owner-gate. 404 → bot doesn't exist; 403 → exists but the
    caller didn't create it. Same surface as the /system-prompt PATCH
    endpoint so mobile can share error handling."""
    inf = await influencer_repo.get_by_id(pool, bot_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Bot not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Only the creator can read or edit this bot"
        )
    return inf


# ─── GET /soul-file ──────────────────────────────────────────────────────


@router.get("/influencers/{bot_id}/soul-file")
async def get_soul_file(bot_id: str, request: Request):
    """Owner-gated. Returns the bot's sections + a version sha mobile
    sends back on PUT for optimistic concurrency.

    Bot without sections → returns a single synthetic fallback section
    wrapping `system_instructions`, with `fallback_to_flat: true`. The
    UI shell stays the same in both modes — only the section count
    differs."""
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _load_owned_influencer(pool, user_id, bot_id)

    sections = _coerce_sections_list(inf.get("system_instructions_sections"))
    fallback = False
    if not sections:
        sections = [_build_fallback_section(inf.get("system_instructions") or "")]
        fallback = True

    return {
        "bot_id": bot_id,
        "display_name": inf.get("display_name") or inf.get("name"),
        "sections": sections,
        "sections_version_sha256": _canonical_sections_sha256(sections),
        "fallback_to_flat": fallback,
    }


# ─── PUT /soul-file ──────────────────────────────────────────────────────


@router.put("/influencers/{bot_id}/soul-file")
async def put_soul_file(bot_id: str, body: dict, request: Request):
    """Owner-gated. Replaces the bot's sections array atomically.

    Body shape:
        {
          "sections": [...],
          "expected_sections_version_sha256": "<sha from the GET>"
        }

    Errors:
      * 403 — non-owner
      * 404 — bot not found
      * 409 stale_sections — sha mismatch; response carries the CURRENT
        sections + sha so mobile can drive the reconcile dialog without
        a re-GET round trip (per #361 mobile-expert refinement)
      * 422 — shape errors (bad slug, duplicate id, empty body, >8
        sections, missing expected sha, etc.)
    """
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _load_owned_influencer(pool, user_id, bot_id)

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="Request body must be a JSON object"
        )
    expected_sha = body.get("expected_sections_version_sha256")
    if not (isinstance(expected_sha, str) and expected_sha.strip()):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_expected_sha",
                "message": (
                    "PUT requires `expected_sections_version_sha256` from "
                    "the most recent GET. Re-GET the soul file and resend."
                ),
            },
        )

    new_sections = _validate_sections_shape(body.get("sections"))

    # Optimistic-concurrency check. Build the current-state view exactly
    # the way GET would so the sha comparison is apples-to-apples: when
    # the bot has no real sections, the sha is over the synthetic
    # fallback section, NOT over the empty list — otherwise the FIRST
    # PUT after creation always 409s because mobile saw a sha over the
    # synthetic section.
    current_sections = _coerce_sections_list(inf.get("system_instructions_sections"))
    current_for_sha = current_sections or [
        _build_fallback_section(inf.get("system_instructions") or "")
    ]
    current_sha = _canonical_sections_sha256(current_for_sha)
    if expected_sha.strip() != current_sha:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_sections",
                "message": (
                    "Sections were edited elsewhere since you opened this "
                    "page. Reload to see the latest, then resend."
                ),
                "current_sections": current_for_sha,
                "current_sections_version_sha256": current_sha,
            },
        )

    await pool.execute(
        """
        UPDATE ai_influencers
        SET system_instructions_sections = $1::jsonb,
            updated_at = NOW()
        WHERE id = $2
        """,
        _json.dumps(new_sections),
        bot_id,
    )

    new_sha = _canonical_sections_sha256(new_sections)
    return {
        "bot_id": bot_id,
        "display_name": inf.get("display_name") or inf.get("name"),
        "sections": new_sections,
        "sections_version_sha256": new_sha,
        "fallback_to_flat": False,
    }
