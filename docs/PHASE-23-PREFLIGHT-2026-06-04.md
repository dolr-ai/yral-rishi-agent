# Phase 23 V1 — synthetic pre-flight result

**Date:** 2026-06-04 03:34 UTC
**Image under test:** `76858dfa` (current production)
**Status:** ✅ **PASS** — end-to-end flow works. 23.7 (Kareena assign + Motorola dogfood) is good to go.

## What was tested

A throwaway influencer with `skill_slug='nutrition_coach'` was inserted directly into production (`preflight-skill-test-2026-06-04`), exercised through two real chat turns by a synthetic user (`preflight-user-A`), then all artifacts were deleted.

## Setup

```sql
INSERT INTO ai_influencers (id, name, display_name, category, system_instructions,
    is_active, is_nsfw, skill_slug, source)
VALUES ('preflight-skill-test-2026-06-04', 'preflight-skill-test', 'Pre-flight Skill Test',
    'advisor', 'You are a test influencer for backend skill verification.',
    'active', false, 'nutrition_coach', 'preflight');
```

Influencer name kept clearly synthetic so a stray prod query couldn't surface it to a real user.

## Turn 1 — `"hi"`

Bot replied:

> Hey there! So glad you're here. To get started, what's your main nutrition goal, any dietary restrictions or preferences I should know about, and what two times work best for your daily check-ins (like 13:00 and 21:00)?

**Onboarding-keyword hits: 5/5** (`goal`, `diet`, `check`, `time`, `restriction`). Onboarding prompt clearly fired.

## Turn 2 — `"I want to lose 5kg, I'm vegetarian, and please check me at 13:00 and 21:00"`

Bot replied:

> Awesome, losing 5kg is a great goal, and being vegetarian is totally manageable! We'll focus on delicious, plant-based meals to help you get there. I've got your check-ins set for 13:00 and 21:00.
>
> Ready to dive into some tasty tips?

**`<skill_state>` in user-facing reply:** ❌ false
**`</skill_state>` in user-facing reply:** ❌ false

The hidden block was successfully suppressed by `skill_parser.parse_skill_state_block` (mobile UX guarantee held).

## State written to DB

```
 user_id          | preflight-user-A
 influencer_id    | preflight-skill-test-2026-06-04
 skill_slug       | nutrition_coach
 status           | active
 next_event_at    | 2026-06-04 09:34:24.356692+00   (NOW + 6h cadence)
 created_at       | 2026-06-04 03:34:24.356978+00
 state (jsonb)    | {
                      "setup": {
                          "diet_type": "vegetarian",
                          "primary_goal": "lose 5kg",
                          "preferred_times": ["13:00", "21:00"]
                      }
                  }
```

All three required setup keys parsed cleanly. `next_event_at` was set to `NOW() + default_cadence_hours` so the Phase 23.6 proactive loop will pick it up on its next tick.

## Cleanup

```sql
DELETE FROM user_skill_state WHERE influencer_id='preflight-skill-test-2026-06-04';
DELETE FROM messages         WHERE conversation_id IN
    (SELECT id FROM conversations WHERE influencer_id='preflight-skill-test-2026-06-04');
DELETE FROM conversations    WHERE influencer_id='preflight-skill-test-2026-06-04';
DELETE FROM ai_influencers   WHERE id='preflight-skill-test-2026-06-04';
```

Verification: all three count queries return 0.

## What this means for 23.7

Backend is ready. Rishi's Motorola flow tomorrow:

1. Create Kareena from mobile (exercises character_generator + wizard — both on Gemini for TTFT per yesterday's revert).
2. Run `UPDATE ai_influencers SET skill_slug='nutrition_coach' WHERE name='kareena';` (or via the SQL helper Rishi has on rishi-deploy).
3. Open Kareena chat on Motorola, send "hi" → expect onboarding ask.
4. Reply with goal + diet + 2 check-in times in natural language.
5. Expect bot acknowledgement + (NO visible `<skill_state>` tag).
6. Wait for the next 15-min engagement-loop tick after `next_event_at` passes (default cadence 6h).

If anything regresses on Kareena vs this pre-flight, the difference is the specific influencer config (system_instructions, category) — pre-flight used `category='advisor'` and a minimal system_instructions. Kareena is likely `companion` (which the catalog says is INCOMPATIBLE with nutrition_coach per the medical-advice rule). Rishi's call to either:
- Override the compatibility hint (it's soft, not enforced)
- Or assign nutrition_coach to a new advisor-archetype influencer instead of Kareena
