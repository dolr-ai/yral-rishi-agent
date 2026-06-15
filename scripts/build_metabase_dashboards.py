#!/usr/bin/env python3
"""Bootstrap Metabase dashboards for yral-rishi-agent v2.

Creates 6 dashboards (Founder Pulse + Users & Retention + Bots & Quality +
Money & Reliability + Safety & Moderation + System Health) with ~40 cards
via the Metabase REST API.

The dashboard called "Conversation Quality — Best & Worst" is NOT
created here — Rishi already built it by hand on 2026-06-15.

Run once:
    export METABASE_API_KEY='mb_xxxxxxxxxxxx'
    python scripts/build_metabase_dashboards.py

Idempotency:
    If any of the 6 target dashboards already exist by name, the script
    aborts and asks you to delete them first. After you delete the
    half-built "Founder Pulse" you started by hand, re-run.

Reproducibility:
    The dashboards + cards are pure SQL + Metabase viz settings. If
    Metabase ever resets, mint a fresh API key, re-run this script,
    same dashboards reappear.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib import error, request

METABASE_URL = os.environ.get("METABASE_URL", "https://metabase.rishi.yral.com")
DATABASE_ID = int(os.environ.get("METABASE_DB_ID", "2"))
API_KEY = os.environ.get("METABASE_API_KEY")

if not API_KEY:
    print("ERROR: METABASE_API_KEY env var not set.", file=sys.stderr)
    print("  Run: export METABASE_API_KEY='mb_...'", file=sys.stderr)
    sys.exit(1)


def api(method: str, path: str, payload: dict | None = None) -> Any:
    url = f"{METABASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(
        url,
        data=data,
        method=method,
        headers={
            "x-api-key": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "{}"
            return json.loads(body)
    except error.HTTPError as e:
        msg = e.read().decode()[:500] if hasattr(e, "read") else str(e)
        print(f"\nERROR {method} {path}: HTTP {e.code} — {msg}", file=sys.stderr)
        raise
    except error.URLError as e:
        print(f"\nERROR {method} {path}: {e}", file=sys.stderr)
        raise


def create_card(
    name: str,
    sql: str,
    display: str = "table",
    viz: dict | None = None,
) -> int:
    body = {
        "name": name,
        "dataset_query": {
            "database": DATABASE_ID,
            "type": "native",
            "native": {"query": sql, "template-tags": {}},
        },
        "display": display,
        "visualization_settings": viz or {},
        "collection_id": None,
    }
    result = api("POST", "/api/card", body)
    return result["id"]


def create_dashboard(name: str, description: str = "") -> int:
    body = {"name": name, "description": description, "collection_id": None}
    return api("POST", "/api/dashboard", body)["id"]


def set_dashcards(dashboard_id: int, dashcards: list[dict]) -> None:
    api("PUT", f"/api/dashboard/{dashboard_id}", {"dashcards": dashcards})


def list_dashboards() -> list[dict]:
    result = api("GET", "/api/dashboard")
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result  # older versions return raw list


# ============================================================
# Dashboard specs — each is (name, description, [cards...])
# Each card is a dict with: name, sql, display, viz (optional),
# and a layout: row, col, w, h on the 24-col grid.
# ============================================================

DASHBOARDS: list[dict] = [
    # -------------------------------------------------------- #2
    {
        "name": "Founder Pulse — Daily Heartbeat",
        "description": "Open every morning. 2-min glance: am I winning today? "
                       "Top row = today vs yesterday. Middle = 30-day trends. "
                       "Bottom = money + reliability.",
        "cards": [
            {
                "name": "Active users today vs yesterday",
                "display": "table",
                "row": 0, "col": 0, "w": 8, "h": 4,
                "sql": """
WITH today AS (
    SELECT COUNT(DISTINCT c.user_id) AS users
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
      AND m.created_at::date = CURRENT_DATE
),
yesterday AS (
    SELECT COUNT(DISTINCT c.user_id) AS users
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
      AND m.created_at::date = CURRENT_DATE - INTERVAL '1 day'
)
SELECT
    (SELECT users FROM today) AS active_users_today,
    (SELECT users FROM yesterday) AS active_users_yesterday,
    ROUND(
        ((SELECT users FROM today)::numeric - (SELECT users FROM yesterday))
        / NULLIF((SELECT users FROM yesterday), 0) * 100, 1
    ) AS pct_change;
""".strip(),
            },
            {
                "name": "Messages today vs yesterday",
                "display": "table",
                "row": 0, "col": 8, "w": 8, "h": 4,
                "sql": """
SELECT
    COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS messages_today,
    COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day') AS messages_yesterday,
    ROUND(
        (COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE)::numeric
        - COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day'))
        / NULLIF(COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day'), 0) * 100, 1
    ) AS pct_change
FROM messages
WHERE created_at >= CURRENT_DATE - INTERVAL '2 days';
""".strip(),
            },
            {
                "name": "New conversations today vs yesterday",
                "display": "table",
                "row": 0, "col": 16, "w": 8, "h": 4,
                "sql": """
SELECT
    COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE AND conversation_type = 'ai_chat') AS new_convos_today,
    COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day' AND conversation_type = 'ai_chat') AS new_convos_yesterday,
    ROUND(
        (COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE AND conversation_type = 'ai_chat')::numeric
        - COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day' AND conversation_type = 'ai_chat'))
        / NULLIF(COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day' AND conversation_type = 'ai_chat'), 0) * 100, 1
    ) AS pct_change
FROM conversations
WHERE created_at >= CURRENT_DATE - INTERVAL '2 days';
""".strip(),
            },
            {
                "name": "DAU — last 30 days",
                "display": "line",
                "row": 4, "col": 0, "w": 12, "h": 7,
                "viz": {"graph.dimensions": ["day"], "graph.metrics": ["daily_active_users"]},
                "sql": """
SELECT
    m.created_at::date AS day,
    COUNT(DISTINCT c.user_id) AS daily_active_users
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE m.role = 'user'
  AND m.created_at > CURRENT_DATE - INTERVAL '30 days'
GROUP BY m.created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Daily messages — last 30 days",
                "display": "line",
                "row": 4, "col": 12, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["total_messages", "user_messages", "ai_messages"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    COUNT(*) AS total_messages,
    COUNT(*) FILTER (WHERE role = 'user') AS user_messages,
    COUNT(*) FILTER (WHERE role = 'assistant') AS ai_messages
FROM messages
WHERE created_at > CURRENT_DATE - INTERVAL '30 days'
GROUP BY created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Hour-of-day chat activity (IST, last 14d)",
                "display": "bar",
                "row": 11, "col": 0, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["hour_of_day", "day_of_week"],
                    "graph.metrics": ["messages"],
                },
                "sql": """
SELECT
    EXTRACT(hour FROM created_at AT TIME ZONE 'Asia/Kolkata')::int AS hour_of_day,
    TRIM(TO_CHAR(created_at AT TIME ZONE 'Asia/Kolkata', 'Day')) AS day_of_week,
    COUNT(*) AS messages
FROM messages
WHERE role = 'user'
  AND created_at > NOW() - INTERVAL '14 days'
GROUP BY hour_of_day, day_of_week
ORDER BY hour_of_day, day_of_week;
""".strip(),
            },
            {
                "name": "D1 retention by signup cohort (last 14 days)",
                "display": "bar",
                "row": 11, "col": 12, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["cohort_day"],
                    "graph.metrics": ["d1_retention_pct"],
                },
                "sql": """
WITH first_day_per_user AS (
    SELECT
        c.user_id,
        MIN(m.created_at::date) AS signup_date
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
    GROUP BY c.user_id
),
cohort AS (
    SELECT
        fdpu.user_id,
        fdpu.signup_date,
        EXISTS (
            SELECT 1 FROM messages m2
            JOIN conversations c2 ON c2.id = m2.conversation_id
            WHERE c2.user_id = fdpu.user_id
              AND m2.role = 'user'
              AND m2.created_at::date = fdpu.signup_date + INTERVAL '1 day'
        ) AS came_back_d1
    FROM first_day_per_user fdpu
    WHERE fdpu.signup_date >= CURRENT_DATE - INTERVAL '14 days'
      AND fdpu.signup_date < CURRENT_DATE
)
SELECT
    signup_date AS cohort_day,
    COUNT(*) AS new_users_that_day,
    COUNT(*) FILTER (WHERE came_back_d1) AS came_back_next_day,
    ROUND(COUNT(*) FILTER (WHERE came_back_d1)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d1_retention_pct
FROM cohort
GROUP BY signup_date
ORDER BY signup_date DESC;
""".strip(),
            },
            {
                "name": "LLM cost today (real $) vs yesterday",
                "display": "table",
                "row": 19, "col": 0, "w": 8, "h": 4,
                "sql": """
SELECT
    COALESCE(SUM(cost_usd) FILTER (WHERE created_at::date = CURRENT_DATE), 0)::numeric(10,2) AS cost_today_usd,
    COALESCE(SUM(cost_usd) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day'), 0)::numeric(10,2) AS cost_yesterday_usd,
    ROUND(
        (COALESCE(SUM(cost_usd) FILTER (WHERE created_at::date = CURRENT_DATE), 0)
        - COALESCE(SUM(cost_usd) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day'), 0))
        / NULLIF(COALESCE(SUM(cost_usd) FILTER (WHERE created_at::date = CURRENT_DATE - INTERVAL '1 day'), 0), 0) * 100, 1
    ) AS pct_change
FROM llm_costs
WHERE provider IN ('gemini', 'openai', 'openrouter')
  AND created_at >= CURRENT_DATE - INTERVAL '2 days';
""".strip(),
            },
            {
                "name": "LLM rejection rate today",
                "display": "table",
                "row": 19, "col": 8, "w": 8, "h": 4,
                "sql": """
SELECT
    COUNT(*) AS total_calls_today,
    COUNT(*) FILTER (WHERE outcome != 'success') AS failed_calls_today,
    ROUND(COUNT(*) FILTER (WHERE outcome != 'success')::numeric / NULLIF(COUNT(*), 0) * 100, 2) AS rejection_rate_pct
FROM llm_costs
WHERE created_at::date = CURRENT_DATE;
""".strip(),
            },
            {
                "name": "Cost per active user (7d)",
                "display": "table",
                "row": 19, "col": 16, "w": 8, "h": 4,
                "sql": """
WITH cost AS (
    SELECT SUM(cost_usd) AS total_cost
    FROM llm_costs
    WHERE created_at > NOW() - INTERVAL '7 days'
      AND provider IN ('gemini', 'openai', 'openrouter')
),
users AS (
    SELECT COUNT(DISTINCT c.user_id) AS active_users
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
      AND m.created_at > NOW() - INTERVAL '7 days'
)
SELECT
    ROUND((SELECT total_cost FROM cost), 2) AS total_cost_7d_usd,
    (SELECT active_users FROM users) AS active_users_7d,
    ROUND((SELECT total_cost FROM cost) / NULLIF((SELECT active_users FROM users), 0), 4) AS cost_per_active_user_usd;
""".strip(),
            },
        ],
    },
    # -------------------------------------------------------- #3
    {
        "name": "Users & Retention",
        "description": "Most important dashboard for an early product. "
                       "Retention curves > everything else. Open Mondays.",
        "cards": [
            {
                "name": "New users per day (last 60 days)",
                "display": "line",
                "row": 0, "col": 0, "w": 24, "h": 7,
                "viz": {
                    "graph.dimensions": ["signup_date"],
                    "graph.metrics": ["new_users"],
                },
                "sql": """
WITH first_message AS (
    SELECT c.user_id, MIN(m.created_at::date) AS signup_date
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
    GROUP BY c.user_id
)
SELECT signup_date, COUNT(*) AS new_users
FROM first_message
WHERE signup_date >= CURRENT_DATE - INTERVAL '60 days'
GROUP BY signup_date
ORDER BY signup_date ASC;
""".strip(),
            },
            {
                "name": "Retention curve by week-of-signup cohort (D1/D3/D7/D14/D30)",
                "display": "table",
                "row": 7, "col": 0, "w": 24, "h": 8,
                "sql": """
WITH first_day AS (
    SELECT c.user_id, MIN(m.created_at::date) AS signup_date
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
    GROUP BY c.user_id
),
all_activity AS (
    SELECT DISTINCT c.user_id, m.created_at::date AS active_date
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
),
cohort_activity AS (
    SELECT
        fd.user_id,
        DATE_TRUNC('week', fd.signup_date)::date AS cohort_week,
        BOOL_OR(aa.active_date = fd.signup_date + 1) AS d1,
        BOOL_OR(aa.active_date = fd.signup_date + 3) AS d3,
        BOOL_OR(aa.active_date = fd.signup_date + 7) AS d7,
        BOOL_OR(aa.active_date = fd.signup_date + 14) AS d14,
        BOOL_OR(aa.active_date = fd.signup_date + 30) AS d30
    FROM first_day fd
    LEFT JOIN all_activity aa ON aa.user_id = fd.user_id
    WHERE fd.signup_date >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY fd.user_id, fd.signup_date
)
SELECT
    cohort_week,
    COUNT(*) AS cohort_size,
    ROUND(COUNT(*) FILTER (WHERE d1)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d1_pct,
    ROUND(COUNT(*) FILTER (WHERE d3)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d3_pct,
    ROUND(COUNT(*) FILTER (WHERE d7)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d7_pct,
    ROUND(COUNT(*) FILTER (WHERE d14)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d14_pct,
    ROUND(COUNT(*) FILTER (WHERE d30)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS d30_pct
FROM cohort_activity
GROUP BY cohort_week
ORDER BY cohort_week DESC;
""".strip(),
            },
            {
                "name": "Conversations per user — distribution",
                "display": "bar",
                "row": 15, "col": 0, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["convo_count_bucket"],
                    "graph.metrics": ["users"],
                },
                "sql": """
WITH per_user AS (
    SELECT user_id, COUNT(*) AS convo_count
    FROM conversations
    WHERE conversation_type = 'ai_chat'
    GROUP BY user_id
)
SELECT
    CASE
        WHEN convo_count = 1 THEN '1'
        WHEN convo_count = 2 THEN '2'
        WHEN convo_count = 3 THEN '3'
        WHEN convo_count BETWEEN 4 AND 5 THEN '4-5'
        WHEN convo_count BETWEEN 6 AND 10 THEN '6-10'
        WHEN convo_count BETWEEN 11 AND 20 THEN '11-20'
        WHEN convo_count BETWEEN 21 AND 50 THEN '21-50'
        ELSE '50+'
    END AS convo_count_bucket,
    COUNT(*) AS users
FROM per_user
GROUP BY convo_count_bucket
ORDER BY MIN(convo_count);
""".strip(),
            },
            {
                "name": "Messages per user — distribution",
                "display": "bar",
                "row": 15, "col": 12, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["msg_count_bucket"],
                    "graph.metrics": ["users"],
                },
                "sql": """
WITH per_user AS (
    SELECT c.user_id, COUNT(*) AS msg_count
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
    GROUP BY c.user_id
)
SELECT
    CASE
        WHEN msg_count BETWEEN 1 AND 2 THEN '1-2'
        WHEN msg_count BETWEEN 3 AND 5 THEN '3-5'
        WHEN msg_count BETWEEN 6 AND 10 THEN '6-10'
        WHEN msg_count BETWEEN 11 AND 25 THEN '11-25'
        WHEN msg_count BETWEEN 26 AND 50 THEN '26-50'
        WHEN msg_count BETWEEN 51 AND 100 THEN '51-100'
        WHEN msg_count BETWEEN 101 AND 500 THEN '101-500'
        ELSE '500+'
    END AS msg_count_bucket,
    COUNT(*) AS users
FROM per_user
GROUP BY msg_count_bucket
ORDER BY MIN(msg_count);
""".strip(),
            },
            {
                "name": "Power users — top 50 by message volume",
                "display": "table",
                "row": 22, "col": 0, "w": 12, "h": 8,
                "sql": """
SELECT
    LEFT(c.user_id, 18) || '...' AS user_id_short,
    COUNT(DISTINCT c.id) AS num_conversations,
    COUNT(m.id) FILTER (WHERE m.role = 'user') AS user_messages_sent,
    COUNT(m.id) FILTER (WHERE m.role = 'assistant') AS ai_messages_received,
    MAX(m.created_at) AS most_recent_message,
    MIN(m.created_at) AS first_message,
    MAX(c.longest_streak_days) AS best_streak,
    c.user_id AS full_user_id
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.conversation_type = 'ai_chat'
GROUP BY c.user_id
ORDER BY user_messages_sent DESC
LIMIT 50;
""".strip(),
            },
            {
                "name": "Churn risk — active 7-14d ago, silent in last 7d",
                "display": "table",
                "row": 22, "col": 12, "w": 12, "h": 8,
                "sql": """
WITH last_activity AS (
    SELECT
        c.user_id,
        MAX(m.created_at) AS last_message_at,
        COUNT(m.id) FILTER (WHERE m.role = 'user') AS lifetime_user_messages
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
    GROUP BY c.user_id
)
SELECT
    LEFT(user_id, 18) || '...' AS user_id_short,
    last_message_at,
    NOW() - last_message_at AS silent_for,
    lifetime_user_messages,
    user_id AS full_user_id
FROM last_activity
WHERE last_message_at < NOW() - INTERVAL '7 days'
  AND last_message_at > NOW() - INTERVAL '14 days'
  AND lifetime_user_messages >= 5
ORDER BY lifetime_user_messages DESC
LIMIT 100;
""".strip(),
            },
        ],
    },
    # -------------------------------------------------------- #4
    {
        "name": "Bots & Quality",
        "description": "Supply-side health: which AI influencers earn their "
                       "keep? Quality drift? Open weekly + after Coach changes.",
        "cards": [
            {
                "name": "Top 20 bots by conversation count (all time)",
                "display": "row",
                "row": 0, "col": 0, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["influencer"],
                    "graph.metrics": ["conversations"],
                },
                "sql": """
SELECT
    ai.display_name AS influencer,
    COUNT(*) AS conversations,
    ai.is_nsfw,
    ai.category
FROM conversations c
JOIN ai_influencers ai ON ai.id = c.influencer_id
WHERE c.conversation_type = 'ai_chat'
GROUP BY ai.display_name, ai.is_nsfw, ai.category
ORDER BY conversations DESC
LIMIT 20;
""".strip(),
            },
            {
                "name": "Top 20 bots by message volume (last 14d)",
                "display": "row",
                "row": 0, "col": 12, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["influencer"],
                    "graph.metrics": ["messages"],
                },
                "sql": """
SELECT
    ai.display_name AS influencer,
    COUNT(m.id) AS messages,
    COUNT(DISTINCT c.user_id) AS unique_users,
    ai.is_nsfw
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
JOIN ai_influencers ai ON ai.id = c.influencer_id
WHERE m.created_at > NOW() - INTERVAL '14 days'
GROUP BY ai.display_name, ai.is_nsfw
ORDER BY messages DESC
LIMIT 20;
""".strip(),
            },
            {
                "name": "Bot quality scores — latest per bot",
                "display": "table",
                "row": 8, "col": 0, "w": 12, "h": 8,
                "sql": """
WITH latest AS (
    SELECT DISTINCT ON (bot_id)
        bot_id,
        score_overall,
        score_in_character,
        score_response_quality,
        score_engagement,
        sample_size,
        created_at AS scored_at
    FROM bot_quality_scores
    ORDER BY bot_id, created_at DESC
)
SELECT
    ai.display_name AS influencer,
    ROUND(l.score_overall::numeric, 2) AS overall,
    ROUND(l.score_in_character::numeric, 2) AS in_character,
    ROUND(l.score_response_quality::numeric, 2) AS response_quality,
    ROUND(l.score_engagement::numeric, 2) AS engagement,
    l.sample_size,
    l.scored_at
FROM latest l
JOIN ai_influencers ai ON ai.id = l.bot_id
ORDER BY l.score_overall DESC;
""".strip(),
            },
            {
                "name": "Quality drift — score over time for top 10 bots",
                "display": "line",
                "row": 8, "col": 12, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["scored_at", "influencer"],
                    "graph.metrics": ["overall"],
                },
                "sql": """
WITH top10 AS (
    SELECT bot_id
    FROM bot_quality_scores
    WHERE created_at > NOW() - INTERVAL '30 days'
    GROUP BY bot_id
    ORDER BY AVG(score_overall) DESC
    LIMIT 10
)
SELECT
    bqs.created_at::date AS scored_at,
    ai.display_name AS influencer,
    ROUND(AVG(bqs.score_overall)::numeric, 2) AS overall
FROM bot_quality_scores bqs
JOIN top10 ON top10.bot_id = bqs.bot_id
JOIN ai_influencers ai ON ai.id = bqs.bot_id
WHERE bqs.created_at > NOW() - INTERVAL '30 days'
GROUP BY bqs.created_at::date, ai.display_name
ORDER BY scored_at ASC;
""".strip(),
            },
            {
                "name": "Inactive bots — no messages in last 30 days",
                "display": "table",
                "row": 16, "col": 0, "w": 12, "h": 8,
                "sql": """
WITH last_msg AS (
    SELECT c.influencer_id, MAX(m.created_at) AS last_message_at
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    GROUP BY c.influencer_id
)
SELECT
    ai.display_name AS influencer,
    ai.category,
    ai.is_active,
    ai.is_nsfw,
    ai.created_at AS created,
    lm.last_message_at,
    NOW() - lm.last_message_at AS silent_for
FROM ai_influencers ai
LEFT JOIN last_msg lm ON lm.influencer_id = ai.id
WHERE ai.is_active = 'active'
  AND (lm.last_message_at IS NULL OR lm.last_message_at < NOW() - INTERVAL '30 days')
ORDER BY ai.created_at DESC
LIMIT 100;
""".strip(),
            },
            {
                "name": "Bot creator leaderboard — top 20 by total bot conversations",
                "display": "row",
                "row": 16, "col": 12, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["creator_principal_short"],
                    "graph.metrics": ["total_conversations_across_bots"],
                },
                "sql": """
SELECT
    LEFT(COALESCE(ai.parent_principal_id, 'SYSTEM'), 18) || '...' AS creator_principal_short,
    COUNT(DISTINCT ai.id) AS bots_created,
    COALESCE(SUM(convs.convo_count), 0) AS total_conversations_across_bots,
    ai.parent_principal_id AS full_principal
FROM ai_influencers ai
LEFT JOIN (
    SELECT influencer_id, COUNT(*) AS convo_count
    FROM conversations
    WHERE conversation_type = 'ai_chat'
    GROUP BY influencer_id
) convs ON convs.influencer_id = ai.id
WHERE ai.parent_principal_id IS NOT NULL
GROUP BY ai.parent_principal_id
ORDER BY total_conversations_across_bots DESC
LIMIT 20;
""".strip(),
            },
        ],
    },
    # -------------------------------------------------------- #5
    {
        "name": "Money & Reliability",
        "description": "LLM cost + LLM errors. The unit economics dashboard. "
                       "Watch rejection_rate + cost-per-active-user weekly.",
        "cards": [
            {
                "name": "Daily LLM cost (real $) — last 30 days",
                "display": "line",
                "row": 0, "col": 0, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["cost_usd"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    ROUND(SUM(cost_usd)::numeric, 2) AS cost_usd
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '30 days'
  AND provider IN ('gemini', 'openai', 'openrouter')
GROUP BY created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Daily synthetic LLM cost (internal_vllm fair-use, 30d)",
                "display": "line",
                "row": 0, "col": 12, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["synthetic_cost_usd"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    ROUND(SUM(cost_usd)::numeric, 4) AS synthetic_cost_usd
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '30 days'
  AND provider NOT IN ('gemini', 'openai', 'openrouter')
GROUP BY created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Cost split by provider (last 7 days)",
                "display": "pie",
                "row": 7, "col": 0, "w": 12, "h": 7,
                "viz": {
                    "pie.dimension": "provider",
                    "pie.metric": "cost_usd",
                },
                "sql": """
SELECT
    provider,
    ROUND(SUM(cost_usd)::numeric, 2) AS cost_usd,
    COUNT(*) AS calls
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY provider
ORDER BY cost_usd DESC;
""".strip(),
            },
            {
                "name": "Cost split by process (last 7 days)",
                "display": "row",
                "row": 7, "col": 12, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["process"],
                    "graph.metrics": ["cost_usd"],
                },
                "sql": """
SELECT
    process,
    ROUND(SUM(cost_usd)::numeric, 2) AS cost_usd,
    COUNT(*) AS calls,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY process
ORDER BY cost_usd DESC;
""".strip(),
            },
            {
                "name": "Daily rejection rate per process (last 14 days)",
                "display": "line",
                "row": 14, "col": 0, "w": 24, "h": 7,
                "viz": {
                    "graph.dimensions": ["day", "process"],
                    "graph.metrics": ["rejection_rate_pct"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    process,
    COUNT(*) AS total_calls,
    COUNT(*) FILTER (WHERE outcome != 'success') AS failed_calls,
    ROUND(COUNT(*) FILTER (WHERE outcome != 'success')::numeric / NULLIF(COUNT(*), 0) * 100, 2) AS rejection_rate_pct
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '14 days'
GROUP BY created_at::date, process
ORDER BY day ASC, process;
""".strip(),
            },
            {
                "name": "Top 20 LLM errors (last 7 days)",
                "display": "table",
                "row": 21, "col": 0, "w": 24, "h": 8,
                "sql": """
SELECT
    LEFT(COALESCE(error_message, '(no error message recorded)'), 200) AS error_preview,
    process,
    provider,
    model,
    COUNT(*) AS occurrences,
    MAX(created_at) AS most_recent
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '7 days'
  AND outcome != 'success'
GROUP BY error_preview, process, provider, model
ORDER BY occurrences DESC
LIMIT 20;
""".strip(),
            },
            {
                "name": "Cost per active user trend (rolling 7d, last 30 days)",
                "display": "line",
                "row": 29, "col": 0, "w": 24, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["cost_per_active_user_usd"],
                },
                "sql": """
WITH days AS (
    SELECT generate_series(CURRENT_DATE - INTERVAL '30 days', CURRENT_DATE, INTERVAL '1 day')::date AS day
),
cost_per_day AS (
    SELECT
        d.day,
        COALESCE((
            SELECT SUM(cost_usd)
            FROM llm_costs lc
            WHERE lc.provider IN ('gemini', 'openai', 'openrouter')
              AND lc.created_at::date > d.day - INTERVAL '7 days'
              AND lc.created_at::date <= d.day
        ), 0) AS rolling_7d_cost,
        COALESCE((
            SELECT COUNT(DISTINCT c.user_id)
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.role = 'user'
              AND m.created_at::date > d.day - INTERVAL '7 days'
              AND m.created_at::date <= d.day
        ), 0) AS rolling_7d_active_users
    FROM days d
)
SELECT
    day,
    ROUND(rolling_7d_cost::numeric, 2) AS rolling_7d_cost_usd,
    rolling_7d_active_users,
    ROUND((rolling_7d_cost / NULLIF(rolling_7d_active_users, 0))::numeric, 4) AS cost_per_active_user_usd
FROM cost_per_day
ORDER BY day ASC;
""".strip(),
            },
        ],
    },
    # -------------------------------------------------------- #6
    {
        "name": "Safety & Moderation",
        "description": "NSFW share, moderation actions, new bot creation. "
                       "Open daily once traffic grows — 30 seconds to scan.",
        "cards": [
            {
                "name": "NSFW vs SFW conversation share over time (30d)",
                "display": "line",
                "row": 0, "col": 0, "w": 24, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["nsfw_share_pct", "sfw_share_pct"],
                },
                "sql": """
SELECT
    c.created_at::date AS day,
    COUNT(*) FILTER (WHERE ai.is_nsfw) AS nsfw_conversations,
    COUNT(*) FILTER (WHERE NOT ai.is_nsfw) AS sfw_conversations,
    ROUND(COUNT(*) FILTER (WHERE ai.is_nsfw)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS nsfw_share_pct,
    ROUND(COUNT(*) FILTER (WHERE NOT ai.is_nsfw)::numeric / NULLIF(COUNT(*), 0) * 100, 1) AS sfw_share_pct
FROM conversations c
JOIN ai_influencers ai ON ai.id = c.influencer_id
WHERE c.conversation_type = 'ai_chat'
  AND c.created_at > NOW() - INTERVAL '30 days'
GROUP BY c.created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Top 20 NSFW bots by traffic (last 14d)",
                "display": "row",
                "row": 7, "col": 0, "w": 12, "h": 8,
                "viz": {
                    "graph.dimensions": ["influencer"],
                    "graph.metrics": ["messages"],
                },
                "sql": """
SELECT
    ai.display_name AS influencer,
    COUNT(m.id) AS messages,
    COUNT(DISTINCT c.user_id) AS unique_users
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
JOIN ai_influencers ai ON ai.id = c.influencer_id
WHERE ai.is_nsfw
  AND m.created_at > NOW() - INTERVAL '14 days'
GROUP BY ai.display_name
ORDER BY messages DESC
LIMIT 20;
""".strip(),
            },
            {
                "name": "Disabled / discontinued bots (moderation actions)",
                "display": "table",
                "row": 7, "col": 12, "w": 12, "h": 8,
                "sql": """
SELECT
    display_name AS influencer,
    category,
    is_active,
    is_nsfw,
    LEFT(COALESCE(parent_principal_id, ''), 18) || '...' AS creator_short,
    created_at,
    updated_at
FROM ai_influencers
WHERE is_active IN ('coming_soon', 'discontinued')
ORDER BY updated_at DESC
LIMIT 50;
""".strip(),
            },
            {
                "name": "New bots created per day (last 60d)",
                "display": "line",
                "row": 15, "col": 0, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["new_bots"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    COUNT(*) AS new_bots,
    COUNT(*) FILTER (WHERE is_nsfw) AS new_nsfw_bots,
    COUNT(*) FILTER (WHERE NOT is_nsfw) AS new_sfw_bots
FROM ai_influencers
WHERE created_at > NOW() - INTERVAL '60 days'
  AND parent_principal_id IS NOT NULL
GROUP BY created_at::date
ORDER BY day ASC;
""".strip(),
            },
            {
                "name": "Bot population summary",
                "display": "table",
                "row": 15, "col": 12, "w": 12, "h": 7,
                "sql": """
SELECT
    COUNT(*) FILTER (WHERE is_active = 'active') AS active_bots,
    COUNT(*) FILTER (WHERE is_active = 'coming_soon') AS coming_soon_bots,
    COUNT(*) FILTER (WHERE is_active = 'discontinued') AS discontinued_bots,
    COUNT(*) FILTER (WHERE is_nsfw) AS nsfw_bots,
    COUNT(*) FILTER (WHERE NOT is_nsfw) AS sfw_bots,
    COUNT(*) FILTER (WHERE parent_principal_id IS NULL) AS system_bots,
    COUNT(*) FILTER (WHERE parent_principal_id IS NOT NULL) AS user_created_bots,
    COUNT(*) AS total_bots
FROM ai_influencers;
""".strip(),
            },
        ],
    },
    # -------------------------------------------------------- #7
    {
        "name": "System Health",
        "description": "ETL lag, integrity checks, background process health. "
                       "Open when something feels off. ETL lag is your "
                       "load-bearing SLA — must stay under 5 min.",
        "cards": [
            {
                "name": "ETL sync state — current snapshot",
                "display": "table",
                "row": 0, "col": 0, "w": 24, "h": 5,
                "sql": """
SELECT *
FROM etl_sync_state
ORDER BY updated_at DESC NULLS LAST
LIMIT 20;
""".strip(),
            },
            {
                "name": "ETL integrity check results (last 7 days)",
                "display": "table",
                "row": 5, "col": 0, "w": 24, "h": 7,
                "sql": """
SELECT *
FROM etl_integrity_results
WHERE verified_at > NOW() - INTERVAL '7 days'
ORDER BY verified_at DESC
LIMIT 100;
""".strip(),
            },
            {
                "name": "ETL files processed per hour (last 24h)",
                "display": "line",
                "row": 12, "col": 0, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["hour"],
                    "graph.metrics": ["files_processed"],
                },
                "sql": """
SELECT
    DATE_TRUNC('hour', processed_at) AS hour,
    COUNT(*) AS files_processed
FROM etl_processed_files
WHERE processed_at > NOW() - INTERVAL '24 hours'
GROUP BY hour
ORDER BY hour ASC;
""".strip(),
            },
            {
                "name": "Recent LLM failures (last 1 hour)",
                "display": "table",
                "row": 12, "col": 12, "w": 12, "h": 7,
                "sql": """
SELECT
    created_at,
    process,
    provider,
    model,
    LEFT(COALESCE(error_message, ''), 150) AS error_preview
FROM llm_costs
WHERE created_at > NOW() - INTERVAL '1 hour'
  AND outcome != 'success'
ORDER BY created_at DESC
LIMIT 50;
""".strip(),
            },
            {
                "name": "Proactive messages — recent activity (7d)",
                "display": "table",
                "row": 19, "col": 0, "w": 12, "h": 7,
                "sql": """
SELECT
    DATE_TRUNC('day', created_at) AS day,
    COUNT(*) AS proactive_messages_sent
FROM proactive_messages
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY day
ORDER BY day DESC;
""".strip(),
            },
            {
                "name": "Influencer creation activity (last 14d)",
                "display": "line",
                "row": 19, "col": 12, "w": 12, "h": 7,
                "viz": {
                    "graph.dimensions": ["day"],
                    "graph.metrics": ["bots_created"],
                },
                "sql": """
SELECT
    created_at::date AS day,
    COUNT(*) AS bots_created
FROM ai_influencers
WHERE created_at > NOW() - INTERVAL '14 days'
GROUP BY created_at::date
ORDER BY day ASC;
""".strip(),
            },
        ],
    },
]


def main() -> int:
    print(f"Metabase URL: {METABASE_URL}")
    print(f"Database ID:  {DATABASE_ID}")
    print()

    # Idempotency check: bail if any target dashboard already exists.
    print("Checking for existing dashboards...")
    target_names = {d["name"] for d in DASHBOARDS}
    try:
        existing = list_dashboards()
    except Exception as e:
        print(f"  Could not list dashboards: {e}", file=sys.stderr)
        return 2
    conflicts = [d for d in existing if d.get("name") in target_names]
    if conflicts:
        print("\nERROR: These dashboards already exist — delete them first:")
        for d in conflicts:
            print(f"  - id={d['id']}  name='{d['name']}'")
        print("\nIn Metabase: open each → click '...' menu → Move to trash.")
        print("Then re-run this script.")
        return 3
    print("  OK — no conflicts.")
    print()

    created = []
    skipped_cards = []

    for spec in DASHBOARDS:
        name = spec["name"]
        print(f"=== Building: {name} ===")
        try:
            dash_id = create_dashboard(name, spec.get("description", ""))
            print(f"  dashboard id = {dash_id}")
        except Exception as e:
            print(f"  FAILED to create dashboard: {e}", file=sys.stderr)
            continue

        dashcards = []
        next_temp_id = -1
        for card_spec in spec["cards"]:
            try:
                card_id = create_card(
                    name=card_spec["name"],
                    sql=card_spec["sql"],
                    display=card_spec.get("display", "table"),
                    viz=card_spec.get("viz", {}),
                )
                print(f"    + card #{card_id}: {card_spec['name']}")
                dashcards.append(
                    {
                        "id": next_temp_id,
                        "card_id": card_id,
                        "row": card_spec["row"],
                        "col": card_spec["col"],
                        "size_x": card_spec["w"],
                        "size_y": card_spec["h"],
                        "parameter_mappings": [],
                        "visualization_settings": {},
                    }
                )
                next_temp_id -= 1
            except Exception as e:
                msg = str(e)
                print(f"    SKIP {card_spec['name']}: {msg[:200]}", file=sys.stderr)
                skipped_cards.append((name, card_spec["name"], msg))

        if dashcards:
            try:
                set_dashcards(dash_id, dashcards)
                print(f"  arranged {len(dashcards)} cards on dashboard")
            except Exception as e:
                print(f"  FAILED to lay out cards: {e}", file=sys.stderr)

        created.append((name, dash_id, len(dashcards)))
        print()

    print("=" * 60)
    print("DONE.")
    print("=" * 60)
    for name, dash_id, n_cards in created:
        url = f"{METABASE_URL}/dashboard/{dash_id}"
        print(f"  {n_cards:>2} cards  {url}  — {name}")
    if skipped_cards:
        print()
        print(f"NOTE: {len(skipped_cards)} cards skipped due to SQL/schema errors:")
        for dash, card, msg in skipped_cards:
            print(f"  - [{dash}] {card}")
            print(f"      {msg[:150]}")
        print("\nThese usually mean the referenced table doesn't exist yet")
        print("(e.g. proactive_messages, etl_processed_files). The dashboards")
        print("were still created; just missing those individual cards.")
    print()
    print("REMINDER: delete the API key now —")
    print("  Admin settings → Authentication → API Keys → delete 'dashboard-bootstrap'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
