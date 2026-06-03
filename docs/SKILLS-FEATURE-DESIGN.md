# Skills Feature — Design Doc for Session 6

**Status:** Design, not yet a phase. To be slotted into the master plan by Session 6 when capacity allows.
**Author:** Session 7 (Coach Strategy spawn, 2026-05-30), after Codex consult + Rishi review.
**V1 target:** One skill (`nutrition_coach`) on one influencer (Kareena), end-to-end.

---

## What this is

YRAL needs to support specialist AI influencers — not just companions. The full list of verticals the system should eventually support: nutrition coaches, daily news briefings, stock market explainers, travel advisors, real estate scouts, running/HYROX/yoga coaches, language tutors, dating coaches, study coaches, creator-growth coaches, and dozens more.

The strategic insight: **don't hand-build each one — build a factory.** Most verticals share the same engine (chat, memory, proactive loop, scoring). What differs is domain expertise, structured user state, and check-in cadence. That delta is what a **skill** captures.

## Mental model: archetype × skill

The existing soul-file system has archetypes (companion, advisor, entertainer, educator, creator) that define **personality and voice**. Skills add an orthogonal axis that defines **domain and job-to-be-done**.

```
Influencer = archetype (personality) × skill (job)

Kareena       = advisor    × nutrition_coach
Rohan-the-bro = entertainer × nutrition_coach    (same skill, different voice)
India News AI = educator   × daily_briefing
Rome Travel   = advisor    × travel_advisor
Sleep Coach   = companion  × sleep_coach
```

Same skill can pair with different archetypes. Same archetype can wear different skills. The two are independent.

**Soft compatibility constraint:** each skill declares which archetypes it pairs cleanly with. Example: `nutrition_coach` declares compatibility with `advisor` and `educator`, but NOT `companion` — because the companion archetype prompt says "never give medical or therapeutic advice," which contradicts the skill's job. This is enforced at creator-side selection time (the UI offers only compatible archetypes), not at runtime.

## Naming (use these everywhere)

| Concept | Name | Where |
|---|---|---|
| Catalog of skills | `skills` | Python dict in `app/services/skills.py` for V1; converts to a DB table when creators edit skills via the Soul File Coach (Phase 7.5 extension) |
| Which skill an influencer has | `ai_influencers.skill_slug` (nullable TEXT) | New column on existing table |
| Per-user structured state for a skilled influencer | `user_skill_state` | New table, one row per `(user_id, influencer_id)` |
| Freeform user facts | `user_memories` | Existing — unchanged |
| Scheduled outreach | `proactive_messages` | Existing — new `trigger_type` values per skill |

Reads naturally: "Kareena has the `nutrition_coach` skill. Rishi's `user_skill_state` with Kareena holds his goal weight and check-in times."

## V1 scope (deliberately small)

Three behaviors, one skill, one influencer:

1. **Store user goal** (free-form + a small structured shape per skill)
2. **Schedule proactive check-ins** based on user-chosen times
3. **Track adherence** via the existing `conversations.current_streak_days` (reused, not duplicated)

V1 skill: `nutrition_coach`. V1 influencer: Kareena. V1 user journey:

- User opens Kareena. First turn, she asks goal + diet + preferred check-in times.
- User replies in natural language ("I want to lose 5kg, I'm vegetarian, check me at 1pm and 9pm").
- Backend parses the reply (LLM with structured-JSON output, same pattern as `services/wizard.py`) and writes `user_skill_state`.
- Each day at the user's chosen times, the existing engagement loop in `services/proactive.py` finds the due check-in and sends a contextual one ("Lunch check — getting protein in today?").
- User reply increments `current_streak_days`.
- On day 7, Kareena sends a "your week" summary as a normal chat message.

That's V1. Everything else is deferred.

## Schema (two migrations)

```sql
-- migration NNN_user_skill_state.sql
CREATE TABLE user_skill_state (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       TEXT NOT NULL,
  influencer_id TEXT NOT NULL REFERENCES ai_influencers(id),
  skill_slug    TEXT NOT NULL,               -- denormalized from influencer for query speed
  state         JSONB NOT NULL DEFAULT '{}', -- skill-defined shape
  next_event_at TIMESTAMPTZ,                 -- coach=next check-in, news=next briefing
  last_event_at TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'active', -- active | paused | done
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, influencer_id)
);
CREATE INDEX idx_user_skill_state_due
  ON user_skill_state(next_event_at)
  WHERE status = 'active';

-- migration NNN+1_ai_influencers_skill.sql
ALTER TABLE ai_influencers ADD COLUMN skill_slug TEXT NULL;
```

Only two columns are typed (`next_event_at`, `status`) because the proactive loop must query them. Everything else lives in JSONB. The JSONB shape is defined by the skill, not by the DB.

**JSONB convention — always split `state` into `setup` and `runtime` sub-objects:**

```json
{
  "setup":   { "primary_goal": "lose 5kg", "diet_type": "vegetarian", "preferred_times": ["13:00", "21:00"] },
  "runtime": { "last_missed_checkin_at": "2026-06-01T13:00:00Z", "current_adherence_notes": "skipped lunch yesterday" }
}
```

Setup is collected once during onboarding and rarely changes. Runtime is mutated by the proactive loop and chat handler as the user engages. Same convention across every skill — without it, JSONB becomes a junk drawer within months. This is a coding convention, not a DB constraint.

**Why no separate streak column:** reuse `conversations.current_streak_days`. That's already "consistency of engagement," which IS V1 adherence. Adding a second streak field forces two places of truth. The day we need "logged-weight streak" distinct from "talked-to-coach streak," add it then.

**Why no `skills` table yet:** for one skill in V1, a Python dict is the symmetric place (mirrors `ARCHETYPE_PROMPTS` in `soul_file.py`). The day creators edit skills via the Soul File Coach UI, we convert to a table. Until then: code.

## Skill catalog shape (Python dict for V1)

```python
# app/services/skills.py
SKILLS = {
    "nutrition_coach": {
        "display_name": "Nutrition Coach",
        "system_prompt_block": "You are a specialist nutrition coach. ...",
        "onboarding_prompt": (
            "On the user's first turn, ask three things in one warm message: "
            "their goal, dietary restrictions, and preferred check-in times. "
            "After they reply, emit a hidden <skill_state>{...}</skill_state> "
            "block with the parsed answers."
        ),
        # Split into setup (collected once during onboarding) and runtime
        # (mutated by the system as the user engages). Documentation only —
        # JSONB itself is not enforced — but the two keys keep the blob from
        # becoming a junk drawer. Every skill follows the same setup/runtime
        # split.
        "state_schema": {
            "setup": [
                "primary_goal",
                "diet_type",
                "target_weight",
                "current_weight",
                "preferred_times",
            ],
            "runtime": [
                "last_missed_checkin_at",
                "last_weekly_summary_at",
                "current_adherence_notes",
            ],
        },
        "compatible_archetypes": ["advisor", "educator"],
        "proactive_kind": "scheduled_checkin",
        "trigger_type": "skill_nutrition_checkin",
        "checkin_prompt": (
            "Generate a short, time-appropriate check-in for this user. "
            "Reference their goal and current state. Keep it under 30 words."
        ),
        "default_cadence_hours": 6,
        "requires_search": False,
    },
}
```

`state_schema` is documentation, not enforcement — JSONB stays flexible. Adding India News AI tomorrow is one dict entry, no migration.

## Soul File composer change

Add a skill layer between archetype and per-influencer:

```python
# app/services/soul_file.py
def compose(
    system_instructions: str,
    category: str | None = None,
    memories: dict | None = None,
    skill_slug: str | None = None,           # NEW
    user_skill_state: dict | None = None,    # NEW
) -> str:
    layers = [GLOBAL_RULES]
    archetype = (category or "").lower().strip()
    if archetype in ARCHETYPE_PROMPTS:
        layers.append(ARCHETYPE_PROMPTS[archetype])
    if skill_slug and skill_slug in SKILLS:
        layers.append(SKILLS[skill_slug]["system_prompt_block"])
    if system_instructions:
        layers.append(system_instructions)
    if user_skill_state:
        layers.append("**Your current plan for this user:**\n" + _format_state(user_skill_state))
    if memories:
        layers.append(...)  # existing
    return LAYER_SEPARATOR.join(layers)
```

Final layer order: `GLOBAL → ARCHETYPE → SKILL → PER_INFLUENCER → USER_STATE → MEMORIES`. Skill sits after archetype so its guidance wins on conflict (LLMs weight later instructions more heavily).

**Do NOT modify `GLOBAL_RULES`.** The existing "1-3 sentences, mobile-first" rule conflicts with delivering meal plans, but the right place to override is inside the skill prompt block (e.g., "When delivering a plan or weekly review, structured lists are appropriate even if longer"). Skill is later in the prompt → it wins. Don't risk regressing non-skilled influencers by editing the global layer.

## Proactive loop change

Extend `services/proactive.py` — do NOT create a new "Check-in Agent" service. The engagement loop in `main.py` already runs every 15 min. Add:

- `find_due_skill_events(pool)` — `SELECT * FROM user_skill_state WHERE status='active' AND next_event_at <= now()`
- `generate_skill_message(state, skill_def)` — calls Gemini with the skill's `checkin_prompt` + current `state` + recent context
- Wire into the existing loop alongside `find_inactive_conversations()` and `find_idle_mid_conversation()`
- After successful delivery: update `last_event_at = now()`, `next_event_at = now() + cadence`

New `trigger_type` values per skill (e.g. `skill_nutrition_checkin`). Mobile push pipeline already handles arbitrary trigger types.

## Files touched + line estimates

| File | Action | ~Lines |
|---|---|---|
| `migrations/NNN_user_skill_state.sql` | new | 25 |
| `migrations/NNN+1_ai_influencers_skill.sql` | new | 10 |
| `app/services/skills.py` | new — SKILLS dict with `nutrition_coach` | 70 |
| `app/services/soul_file.py` | add skill layer + user_skill_state layer | 20 |
| `app/repositories/skill_state_repo.py` | new — get / upsert / list_due | 90 |
| `app/services/proactive.py` | add `find_due_skill_events` + `generate_skill_message` | 70 |
| `app/routes/skills.py` | new — 3 endpoints (POST state, GET state, PATCH preferences) | 90 |
| `app/models.py` | DTOs for skill state | 30 |
| `app/routes/chat.py` | wire skill_slug + user_skill_state into `soul_file.compose()` call | 15 |

**Total: ~420 lines.** Above the CLAUDE.md 100-line ceiling. Worth Rishi's explicit sign-off before starting, but every line is load-bearing for the skill framework, not just nutrition-specific.

## How V1 scales to the broader vertical list

Once V1 ships, each new skill is one `SKILLS` dict entry + maybe a check-in prompt template. Schema stays put. Example next skills:

| Skill slug | Archetypes | State JSON shape | Proactive kind |
|---|---|---|---|
| `daily_briefing` (India News, Stock Market, etc.) | educator, entertainer | `{topics, region, delivery_time}` | scheduled_briefing |
| `travel_advisor` (Rome, Dubai, Tokyo) | advisor, educator | `{destination, dates, budget, interests}` | event_driven |
| `real_estate_advisor` | advisor | `{city, budget_min, budget_max, type}` | scheduled_briefing |
| `running_coach`, `hyrox_coach`, etc. | advisor, entertainer | `{race_date, current_volume, target_time}` | scheduled_checkin |
| `language_coach` (English, Spanish, etc.) | educator | `{target_language, level, daily_minutes}` | scheduled_checkin |
| `study_coach` | educator, advisor | `{exam, exam_date, subjects, daily_hours}` | scheduled_checkin |
| `creator_growth_coach` | creator, advisor | `{platform, niche, current_followers, goal}` | scheduled_briefing |

None of these need a new table or new service file. They need a `SKILLS` dict entry, sometimes a custom check-in prompt, occasionally a `requires_search: True` flag (when search lands).

## What's explicitly NOT in V1

- **No `skills` DB table** (Python dict only). Converts to table when Soul File Coach UI lets creators edit skills.
- **No web search / retrieval** — needed for news, real estate, travel skills. Defer until Phase 15 lands or until skill demand forces it. Skill schema includes `requires_search` flag so future-search-capable skills are a known dimension.
- **No structured tool calls** (food logging, workout logging) — wait for Phase 15 (Skill Runtime + MCP).
- **No photo analysis** — meal photos, form analysis. Image plumbing exists; specialist analysis quality work doesn't.
- **No mobile onboarding screens** — first-turn LLM onboarding parses user's natural reply into `state`. Zero net-new mobile work for V1.
- **No skill marketplace** — internal-only skill creation at first.
- **No skill chip on influencer card** (good idea, deferred — would help users pick at a glance; pure mobile work).
- **No separate streak field** — reuse `conversations.current_streak_days`.

## Compatibility with existing systems

| System | How skills interact |
|---|---|
| `services/soul_file.py` | New layer added between archetype and per-influencer. Skill prompt block is byte-identical for byte-identical inputs → prompt-cache friendly. |
| `services/memory.py` (`user_memories`) | Unchanged. Memories remain freeform facts ("vegetarian", "knee pain"). Skill state holds structured plan ("target_weight: 75"). Clear boundary — don't mix. |
| `services/session_memory.py` (Redis) | Unchanged. Short-lived signals ("user reported low energy today") feed into check-in generation. |
| `services/proactive.py` | Extended with skill-due-event detection. Same engagement loop. |
| `services/quality_scorer.py` | Unchanged in V1. Existing scoring still applies. Later: add outcome-based metrics (check-in reply rate, adherence trend). |
| `services/wizard.py` | Reuse the JSON-out-of-LLM-text parser pattern (`_extract_json`) for first-turn onboarding. |
| Soul File Coach (Phase 7.5) | Creators edit per-influencer text on top of the skill prompt. They do NOT edit the skill itself in V1. When skill-editing-via-coach ships, that's when `SKILLS` becomes a table. |
| Push notifications | Existing path. New `trigger_type` values like `skill_nutrition_checkin` flow through unchanged. |
| `services/llm_registry.py` | One shared `skill_chat` process serves every skilled influencer — `nutrition_coach`, `english_coach`, `daily_briefing`, etc. Adding a new skill is a one-line `SKILLS` entry, **not** a new registry process. Default `skill_chat` provider is `gemini` (creator + user-facing; TTFT matters — same reasoning as `soul_file_coach`). Per-skill provider specialization is a future option via `PATCH /admin/llm-routing` if cost or latency profiles diverge enough to justify a split; V1 deliberately does not pre-split. |

## First-turn onboarding — the highest-risk piece, harden it explicitly

The "elegant" path is: Kareena asks three things on turn 1, the user replies in natural language, the LLM emits a hidden `<skill_state>{...}</skill_state>` block, the route parses it and writes the row. This works on the happy path. It is also the **least deterministic part of the whole design** and the most likely place for production weirdness.

Treat parser hardening as a first-class requirement, not a polish item. The implementation must include all of:

1. **Strict parser, tolerant of wrapping prose.** Mirror `services/wizard.py:_extract_json` — find the tag boundaries, JSON-decode just the slice, never `eval`.
2. **Per-field validation.** Each extracted field is validated against the skill's `state_schema.setup` shape. Unknown keys are dropped, not stored.
3. **Partial-extraction is OK.** If 2 of 3 fields parse cleanly, write what we got and mark `status='onboarding_partial'`. Kareena's next turn asks for the missing pieces conversationally. Don't reject the whole row.
4. **Parse-failure fallback.** If nothing parses, do NOT write `user_skill_state` and do NOT pretend onboarding succeeded. The route returns the LLM's normal reply (without the hidden tag); Kareena's next turn re-asks more concretely. Log to Sentry with the raw LLM output for tuning.
5. **One retry-with-repair.** On parse failure, the route can call the LLM once more with `"Your previous reply was missing the <skill_state> block. Please reply again with the tag."` — at most one retry to avoid runaway cost.
6. **Idempotency.** Re-running onboarding (user starts the conversation over) overwrites `state.setup` but never deletes `state.runtime`. Streaks and history survive.

Codex's flag on this is correct: "looks magical in happy path, messy in production." The mitigations above are what convert "magical demo" into "shippable."

## What "creator-editable skills" will actually require (later phase, not V1)

The doc says skills become a DB table the day creators edit them via the Soul File Coach. That's correct as a direction, but it is **not a trivial migration** — Session 6 should not budget it as "half a day." When that phase lands it requires:

- **Versioning.** Every skill edit creates a new version row. Influencers reference a specific version. Rolling back is changing the reference, not mutating in place.
- **Ownership.** A skill is either system-owned (built-in `nutrition_coach`) or creator-owned. Permission checks on edit.
- **Compatibility validation.** Editing a skill must not break influencers already using it — required `state_schema` fields can't be removed without a migration plan.
- **Audit history.** Who changed what, when, and why (the coach-chat transcript that produced the edit).
- **Base-vs-override split.** Likely: a base skill defines the floor, and each influencer can override specific blocks (e.g., the check-in prompt) without forking the whole skill.
- **Rollback path.** One-click revert when a creator's edit tanks quality scores.

Realistic estimate when this phase comes up: **5-8 days**, not 1-2. Session 6 should book it accordingly.

## Open questions for the implementer

1. **Where the orchestration lives for the first turn** — chat.py needs to detect "this is the user's first message AND the influencer has a skill AND state is empty" → use the skill's onboarding prompt instead of normal chat flow. Cleanest hook is probably in chat.py before the soul file compose. Confirm.

2. **Should `next_event_at` be set immediately after onboarding, or only after the user confirms?** Edge case if user says "check me at 1pm" but it's already 2pm — set next_event_at to tomorrow 1pm, or today + cadence?

3. **Streak handling for skipped check-ins** — if Kareena sends a check-in and user doesn't reply, does the streak break? Or only break if user is inactive for > 24h? Recommend: reuse the existing streak logic from Phase 5.6 verbatim; don't fork it.

4. **Skill assignment in V1** — internal-only via admin SQL, or do creators pick from a dropdown in the influencer-create wizard? My read: internal-only is one less mobile screen. Confirm.

## Where this slots into the master plan

This is a new phase. Suggested name: **Phase 23 — Skills Framework**. Sub-phases:

| # | Sub-phase | Est. days |
|---|---|---|
| 23.1 | Migrations: `user_skill_state` table + `ai_influencers.skill_slug` column | 0.5 |
| 23.2 | `app/services/skills.py` — SKILLS dict with `nutrition_coach` | 0.5 |
| 23.3 | `app/services/soul_file.py` — add skill + user_skill_state layers | 0.5 |
| 23.4 | `app/repositories/skill_state_repo.py` | 0.5 |
| 23.5 | `app/routes/skills.py` — state endpoints + first-turn onboarding hook in chat.py | 1 |
| 23.6 | `app/services/proactive.py` — find_due_skill_events + generate_skill_message | 1 |
| 23.7 | Assign Kareena `skill_slug=nutrition_coach` and test end-to-end on Motorola | 0.5 |
| **Phase 23 total** | | **~4.5 days** |

After 23.7 is live and tested with one user (Rishi himself), the framework is proven. Phase 24+ becomes "add second skill" (likely `daily_briefing` for an India News influencer), which should take < 1 day per skill.

## One-line strategic summary

YRAL Agent v2 already has the bones of a Coach OS. The skill framework adds the smallest thin layer that turns it into an Expert Factory — one Python dict + one new table + one prompt-composer line — and that layer is what lets the same engine power nutrition coaches, news briefings, travel advisors, real estate scouts, language tutors, and the long tail of verticals without hand-tuning each one.
