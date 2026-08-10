import json
import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row)


async def get_by_id(pool, influencer_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides, system_instructions_sections
        FROM ai_influencers WHERE id = $1
        """,
        influencer_id,
    )
    return _row_to_dict(row) if row else None


async def get_by_name(pool, name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides, system_instructions_sections
        FROM ai_influencers WHERE name = $1
        """,
        name,
    )
    return _row_to_dict(row) if row else None


async def get_active_nsfw_id_by_name(pool, name: str) -> str | None:
    """Return the influencer_id for a name filtered to `is_active='active'`
    AND `is_nsfw=TRUE`. Used by track 2b's /spicy/context to resolve
    amorae's `bot_handle` URL param.

    The catalog has multiple "Tara" rows today — only ONE is the
    amorae-facing NSFW Tara (`taaarraaah`). The is_nsfw filter picks
    the right row regardless of which "tara" comes through the URL,
    and documents the invariant that amorae only ever sees is_nsfw
    bots. Deterministic ORDER BY created_at ASC LIMIT 1 breaks any
    future duplicate deterministically."""
    row = await pool.fetchrow(
        """
        SELECT id FROM ai_influencers
        WHERE name = $1 AND is_active = 'active' AND is_nsfw = TRUE
        ORDER BY created_at ASC
        LIMIT 1
        """,
        name,
    )
    return row["id"] if row else None


async def get_by_id_or_name(pool, id_or_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides, system_instructions_sections
        FROM ai_influencers WHERE id = $1 OR name = $1 LIMIT 1
        """,
        id_or_name,
    )
    return _row_to_dict(row) if row else None


async def get_parent_principal(pool, influencer_id: str) -> str | None:
    row = await pool.fetchrow(
        "SELECT parent_principal_id FROM ai_influencers WHERE id = $1",
        influencer_id,
    )
    if row and row["parent_principal_id"]:
        return row["parent_principal_id"]
    return None


async def get_with_conversation_count(pool, influencer_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT i.id, i.name, i.display_name, i.avatar_url, i.description,
               i.category, i.system_instructions, i.personality_traits,
               i.initial_greeting, i.suggested_messages,
               i.is_active, i.is_nsfw, i.parent_principal_id, i.source,
               i.created_at, i.updated_at, i.metadata, i.skill_slug,
               i.global_rule_overrides, i.system_instructions_sections,
               COUNT(c.id) as conversation_count
        FROM ai_influencers i
        LEFT JOIN conversations c ON i.id = c.influencer_id
        WHERE i.id = $1
        GROUP BY i.id
        """,
        influencer_id,
    )
    return _row_to_dict(row) if row else None


async def list_all(
    pool, limit: int = 50, offset: int = 0, surfaces: tuple[str, ...] | None = None
) -> list[dict]:
    """`surfaces=None` means no surface filter — the query is then identical
    to the pre-2026-08-10 one, so existing callers are unaffected. When a
    filter IS requested it is applied in SQL rather than in Python, so
    `count_all` and the page contents agree; filtering after LIMIT would
    return short pages and a total that doesn't match."""
    rows = await pool.fetch(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides, system_instructions_sections, surface
        FROM ai_influencers
        WHERE is_active != 'discontinued'
          AND ($3::text[] IS NULL OR surface = ANY($3::text[]))
        ORDER BY CASE is_active
            WHEN 'active' THEN 1
            WHEN 'coming_soon' THEN 2
        END, created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
        list(surfaces) if surfaces else None,
    )
    return [_row_to_dict(r) for r in rows]


async def count_all(pool, surfaces: tuple[str, ...] | None = None) -> int:
    """Must apply the same predicate as `list_all` or pagination lies."""
    if surfaces:
        return await pool.fetchval(
            """
            SELECT COUNT(*) FROM ai_influencers
            WHERE is_active != 'discontinued' AND surface = ANY($1::text[])
            """,
            list(surfaces),
        )
    return await pool.fetchval(
        "SELECT COUNT(*) FROM ai_influencers WHERE is_active != 'discontinued'"
    )


async def list_trending(pool, limit: int = 50, offset: int = 0) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.display_name, i.avatar_url, i.description,
               i.category, i.system_instructions, i.personality_traits,
               i.initial_greeting, i.suggested_messages,
               i.is_active, i.is_nsfw, i.parent_principal_id, i.source,
               i.created_at, i.updated_at, i.metadata, i.skill_slug,
               i.global_rule_overrides, i.system_instructions_sections,
               COALESCE(s.conversation_count, 0) AS conversation_count,
               COALESCE(s.message_count, 0)      AS message_count
        FROM ai_influencers i
        LEFT JOIN influencer_trending_stats s ON s.influencer_id = i.id
        WHERE i.is_active = 'active'
        ORDER BY message_count DESC, i.created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    return [_row_to_dict(r) for r in rows]


async def count_trending(pool) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM ai_influencers WHERE is_active = 'active'"
    )


async def create(pool, influencer: dict) -> dict:
    await pool.execute(
        """
        INSERT INTO ai_influencers (
            id, name, display_name, avatar_url, description, category,
            system_instructions, personality_traits, initial_greeting,
            suggested_messages, is_active, is_nsfw, parent_principal_id,
            source, metadata
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        ON CONFLICT (id) DO NOTHING
        """,
        influencer["id"],
        influencer["name"],
        influencer["display_name"],
        influencer.get("avatar_url"),
        influencer.get("description"),
        influencer.get("category"),
        influencer["system_instructions"],
        json.dumps(influencer.get("personality_traits") or {}),
        influencer.get("initial_greeting"),
        json.dumps(influencer.get("suggested_messages") or []),
        influencer.get("is_active", "active"),
        influencer.get("is_nsfw", False),
        influencer.get("parent_principal_id"),
        influencer.get("source"),
        json.dumps(influencer.get("metadata") or {}),
    )
    return await get_by_id(pool, influencer["id"])


async def update_system_prompt(pool, influencer_id: str, instructions: str):
    await pool.execute(
        """
        UPDATE ai_influencers
        SET system_instructions = $1, updated_at = NOW()
        WHERE id = $2
        """,
        instructions,
        influencer_id,
    )


async def get_lora_trigger_word(pool, influencer_id: str) -> str | None:
    """Return the LoRA trigger word for a bot from
    `ai_influencers.metadata.lora_trigger_word`, else None.

    Used by services.theme_generator: without a trigger word, the LoRA
    can't lock identity → generic-lookalike outputs ship (2026-07-06
    bug). Per-bot metadata beats a hardcoded Python dict — new bots
    don't need a code push, and Rishi can rename triggers hot-editably.

    Value is expected to match the exact-case trigger baked into the
    LoRA training (e.g. `TAARA` for Tara's v1 model). Case matters."""
    row = await pool.fetchrow(
        """
        SELECT metadata->>'lora_trigger_word' AS trigger_word
        FROM ai_influencers
        WHERE id = $1
        """,
        influencer_id,
    )
    if not row:
        return None
    tw = row["trigger_word"]
    if not isinstance(tw, str) or not tw.strip():
        return None
    return tw.strip()


async def cache_plain_english_summary(pool, influencer_id: str, summary: dict) -> None:
    """Coach Fix 2 backend — persist the LLM-generated bullet summary
    into `metadata.plain_english_summary` + a parallel
    `metadata.summary_generated_at` timestamp for cheap staleness checks.

    Uses jsonb_set so the rest of metadata (whatever else lives there)
    is preserved. Does NOT touch the row's updated_at — that would
    immediately invalidate the cache we just wrote."""
    await pool.execute(
        """
        UPDATE ai_influencers
        SET metadata = COALESCE(metadata, '{}'::jsonb)
                       || jsonb_build_object(
                              'plain_english_summary', $1::jsonb,
                              'summary_generated_at', $2::text
                          )
        WHERE id = $3
        """,
        json.dumps(summary),
        summary.get("generated_at"),
        influencer_id,
    )


async def soft_delete(pool, influencer_id: str):
    await pool.execute(
        """
        UPDATE ai_influencers
        SET is_active = 'discontinued', display_name = 'Deleted Bot',
            updated_at = NOW()
        WHERE id = $1
        """,
        influencer_id,
    )


async def ban(pool, influencer_id: str):
    await pool.execute(
        """
        UPDATE ai_influencers
        SET is_active = 'discontinued', updated_at = NOW()
        WHERE id = $1
        """,
        influencer_id,
    )


async def unban(pool, influencer_id: str):
    await pool.execute(
        """
        UPDATE ai_influencers
        SET is_active = 'active', updated_at = NOW()
        WHERE id = $1
        """,
        influencer_id,
    )
