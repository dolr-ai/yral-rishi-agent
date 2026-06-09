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
               global_rule_overrides
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
               global_rule_overrides
        FROM ai_influencers WHERE name = $1
        """,
        name,
    )
    return _row_to_dict(row) if row else None


async def get_by_id_or_name(pool, id_or_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides
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
               i.global_rule_overrides,
               COUNT(c.id) as conversation_count
        FROM ai_influencers i
        LEFT JOIN conversations c ON i.id = c.influencer_id
        WHERE i.id = $1
        GROUP BY i.id
        """,
        influencer_id,
    )
    return _row_to_dict(row) if row else None


async def list_all(pool, limit: int = 50, offset: int = 0) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               system_instructions, personality_traits, initial_greeting,
               suggested_messages, is_active, is_nsfw, parent_principal_id,
               source, created_at, updated_at, metadata, skill_slug,
               global_rule_overrides
        FROM ai_influencers
        WHERE is_active != 'discontinued'
        ORDER BY CASE is_active
            WHEN 'active' THEN 1
            WHEN 'coming_soon' THEN 2
        END, created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    return [_row_to_dict(r) for r in rows]


async def count_all(pool) -> int:
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
               i.global_rule_overrides,
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
