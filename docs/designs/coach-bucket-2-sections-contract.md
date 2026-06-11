# Coach Bucket 2 contract — Soul File sections + sectioned proposals

**Audience:** mobile expert (primary), Rishi (review)
**Date:** 2026-06-12
**Status:** Backend contract for the mobile Soul File page. Mobile CANNOT start the page until this lands. Backend will land it this week.

## 1. What changes (90 second version)

Today every bot's personality lives as one opaque blob of text in `ai_influencers.system_instructions`. Creators can ask Coach to edit it, but the result is always a full-text rewrite that ships back as one big "proposed_changes" string. That's hard to read, hard to validate, and forces Coach to claim it's making "surgical edits" while actually doing rewrites.

**Bucket 2 makes the SECTION the unit of work.** A bot's personality is now an ordered list of sections, each with a `heading` + `body`. Coach proposes changes against ONE section at a time. Mobile renders the bot's profile as a list of section cards, taps one to coach against it, and applies section-scoped proposals (one section's body updated, the rest untouched).

Backwards-compatible: bots without sections fall back to the flat `system_instructions` exactly as today. Mobile rolls out behind a feature flag.

## 2. Data model

### New column on `ai_influencers`

```sql
ALTER TABLE ai_influencers
    ADD COLUMN system_instructions_sections JSONB NOT NULL DEFAULT '[]'::jsonb;
```

Shape:

```json
[
  {
    "id": "core_personality",
    "heading": "Core personality",
    "body": "You are Tara, a warm 22-year-old college student who...",
    "editable": true
  },
  {
    "id": "voice_and_tone",
    "heading": "Voice and tone",
    "body": "Sassy when the user flirts; gentle when they're upset...",
    "editable": true
  },
  {
    "id": "platform_rules_reminder",
    "heading": "Platform rules (read-only)",
    "body": "Stay in character. Don't reveal AI nature. Etc.",
    "editable": false
  }
]
```

| Field | Why |
|---|---|
| `id` | Stable slug Coach + mobile reference in proposals. Lowercase snake_case. Unique within a bot. Generated server-side on section create. |
| `heading` | Display label. Editable in the GET/PUT contract; mobile renders as the section card title. |
| `body` | The instruction text the LLM sees at chat time. THE thing Coach edits. |
| `editable` | When false, section is read-only in mobile UI + Coach refuses to propose changes against it (e.g. platform-rules section). |

Default `'[]'::jsonb` so the migration is metadata-only (no row rewrite on the 3,941 ai_influencers rows).

### Migration (next free is `038` after PR-3's 035 + 036/037 placeholders)

```sql
-- migrations/038_ai_influencers_sections.sql
ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS system_instructions_sections JSONB NOT NULL
        DEFAULT '[]'::jsonb;
```

**Migration is purely additive.** No backfill. Bots without sections keep using `system_instructions` (flat text) until the creator opts in via the Soul File page.

### Feature flag

```python
# app/config.py
COACH_SECTIONED_V2_ENABLED = _env_bool("COACH_SECTIONED_V2_ENABLED", False)
```

Default OFF. When OFF:
- `compose()` ignores the sections column and uses flat `system_instructions` as today.
- Coach META_PROMPT renders the flat-text shape; proposals are full-text (today's behavior).
- GET `/soul-file` returns flat text wrapped in a single synthetic section so mobile's contract stays consistent.
- PUT `/soul-file` writes back to flat `system_instructions` (sections column stays `[]`).

When ON:
- `compose()` prefers sections if `len(sections) > 0`; falls back to flat text otherwise.
- Coach proposes against a specific section.
- GET/PUT use the sections shape directly.

Mobile can be rolled out on a per-bot basis later by extending the flag to a per-bot column, but the contract starts with a global flag for the V1 cutover.

## 3. compose() — sections-aware

```python
def compose(
    system_instructions: str,
    *,
    sections: list[dict] | None = None,
    category: str | None = None,
    memories: dict | None = None,
    skill_slug: str | None = None,
    user_skill_state: dict | None = None,
    global_rule_overrides: dict | None = None,
) -> str:
    """L1 GLOBAL → L2 ARCHETYPE → L3 SKILL → L4 PER_INFLUENCER →
    L5 USER_SKILL_STATE → L6 MEMORIES.

    Bucket 2: L4 (per-influencer) renders FROM `sections` if present
    AND coach_sectioned_v2 is enabled. Sections are concatenated by
    heading in order, with the section body as the rendered block.
    Otherwise falls back to flat `system_instructions` (today's path).
    The L1-3 + L5-6 layers are unchanged.
    """
```

Logic when sections are active:

```
== Core personality ==
You are Tara, a warm 22-year-old...

== Voice and tone ==
Sassy when the user flirts...

== Platform rules ==
Stay in character. ...
```

`compose()` is the only consumer of sections at chat time. Everything else (rate limits, audit trail, override flow) is unchanged.

## 4. Coach service — sectioned proposals

### New proposal shape

When `COACH_SECTIONED_V2_ENABLED=true` AND the bot has non-empty `sections`, Coach emits:

```json
{
  "summary": "Make Tara's voice less corporate when she's flirting",
  "proposed_section_change": {
    "section_id": "voice_and_tone",
    "new_body": "When flirty: drop into a more playful, lowercase register. Use 1-2 emojis max. When emotional: warm, no slang.",
    "previous_body_sha256": "<sha of body as Coach read it>"
  },
  "reasoning": "Recent conversations show Tara matches user energy on serious topics but stays corporate when they tease her."
}
```

| Field | Why |
|---|---|
| `section_id` | The exact id from the GET /soul-file response. Coach won't propose against ids it didn't see. |
| `new_body` | The COMPLETE new body for that section (not a diff — same as today's `proposed_changes`). |
| `previous_body_sha256` | Optimistic concurrency. /apply rejects if the section body changed since Coach read it (`409 stale_proposal`). |

### Coach proposal shape catalog (after Bucket 2 lands)

| Shape | When | What `/apply` does |
|---|---|---|
| `proposed_changes` (text) | sections OFF or bot has no sections — today's behavior | UPDATE `ai_influencers.system_instructions` |
| `proposed_section_change` ({section_id, new_body, previous_body_sha256}) | sections ON + bot has sections | UPDATE the section's body inside `system_instructions_sections` |
| `proposed_global_rule_override` ({key, value}) | always — Coach Fix 1 PR-B | merge into `ai_influencers.global_rule_overrides` |

These are mutually exclusive — Coach emits EXACTLY one per turn. `/apply` dispatches on which is set, same as today's two-shape dispatch.

### META_PROMPT change (sections-aware)

When sections are active, the prompt block that's currently:

```
Current Soul File (system_instructions):
"""
{current_instructions}
"""
```

becomes:

```
Current Soul File (sections — propose against ONE):
== {sec[0].heading} == [id={sec[0].id}, editable={sec[0].editable}]
{sec[0].body}

== {sec[1].heading} == [id={sec[1].id}, editable={sec[1].editable}]
{sec[1].body}
...

Rules:
- Identify which section your suggestion applies to.
- Propose against ONE section per turn — refuse multi-section rewrites.
- Refuse to propose against sections marked editable=false.
- The proposed_section_change JSON MUST include the section's `id` exactly as shown.
```

`coach_reply` returns a 5-tuple now (current 4-tuple + `proposed_section_change: dict | None`):

```python
async def coach_reply(...) -> tuple[
    str,                # display_content
    str | None,         # proposed_changes (flat text)
    str | None,         # reasoning
    dict | None,        # proposed_global_rule_override
    dict | None,        # proposed_section_change (Bucket 2)
]:
```

Exactly one of `proposed_changes` / `proposed_global_rule_override` / `proposed_section_change` is non-None when Coach commits to a proposal; all three None for plain text.

### Storage

New columns on `coach_messages` (Bucket 2 migration 039):

```sql
ALTER TABLE coach_messages
    ADD COLUMN proposed_section_change JSONB,
    ADD COLUMN target_section_id VARCHAR(64);  -- denormalized for filter queries
```

Existing `proposed_changes` + `proposed_global_rule_override` + `status` (PR-3) stay. `target_section_id` is the cheap index path for "show me all proposals against the voice_and_tone section."

## 5. Endpoints

### `GET /api/v1/influencers/{bot_id}/soul-file`

Owner-gated (creator must own the bot).

```json
{
  "bot_id": "...",
  "display_name": "Tara",
  "sections": [
    {"id": "core_personality", "heading": "Core personality", "body": "...", "editable": true},
    {"id": "voice_and_tone",   "heading": "Voice and tone",   "body": "...", "editable": true}
  ],
  "sections_version_sha256": "<sha of the sections array>",
  "fallback_to_flat": false
}
```

- `fallback_to_flat: true` means the bot still uses flat `system_instructions` (sections empty or flag OFF). Response then includes `system_instructions` as a synthetic single section so mobile renders consistently.
- `sections_version_sha256` is mobile's optimistic-concurrency handle for PUT.

### `PUT /api/v1/influencers/{bot_id}/soul-file`

Owner-gated. Body:

```json
{
  "sections": [...],
  "expected_sections_version_sha256": "<the sha mobile got from GET>"
}
```

- 409 `stale_sections` if `expected_sections_version_sha256` doesn't match current. Mobile re-GETs + reconciles.
- 422 if any `id` is not a valid slug OR if section ordering is inconsistent (duplicates, missing ids, etc.).
- 403 if creator doesn't own the bot.
- 200 with the new state + new sha on success.

PUT is the path the Soul File page uses for direct editing. Coach proposals go through `/apply` instead (server-side dispatch sets the new body).

### `POST /api/v1/creator/coach/conversations/{coach_conversation_id}/apply`

Existing endpoint (PR-3). Body:

```json
{ "proposal_id": "<coach_messages.id>" }
```

Server reads the row, dispatches on which proposal column is set:

- `proposed_changes` (flat text) → UPDATE `ai_influencers.system_instructions` (today's behavior).
- `proposed_global_rule_override` → JSONB merge into `ai_influencers.global_rule_overrides` (today's behavior).
- **NEW** `proposed_section_change` → UPDATE the section body inside `system_instructions_sections`. Validates `previous_body_sha256` matches current; 409 `stale_proposal` if drifted.

PR-3's lifecycle (status pending → applied + supersede) applies to ALL three shapes identically.

## 6. Mobile UX contract

Mobile builds:

1. **Bot profile screen** — taps "Edit Soul File" → opens Soul File page.
2. **Soul File page** — list of section cards (heading + body excerpt + edit button). Tap a card to:
   - Edit directly (writes via PUT)
   - Open Coach against this section (passes `section_id` to the Coach session as a hint — see hint format below)
3. **Coach session** — same as today's Coach UX, but proposals show "Section: Voice and tone" badge so the creator knows scope.
4. **Save button gating** — PR-4's `pending_proposal_exists` field works unchanged; PR-3's typed status drives card-state rendering.

### Section hint to Coach

When mobile opens Coach from a section tap, it passes an extra field on `POST /conversations/{bot_id}`:

```json
{
  "fresh": false,
  "section_hint": "voice_and_tone"
}
```

Server adds this to the opening prompt: *"The creator opened Coach against the 'Voice and tone' section. Default your proposals to that section unless they explicitly ask about a different one."*

Backwards-compatible: `section_hint` is optional. Existing Coach flow (open without a hint) still works.

## 7. What this contract does NOT change

- Existing `/apply` shape, behavior, lifecycle (PR-3).
- Existing `/discard` endpoint (PR-3).
- Existing Coach session create / send-message flow (only the proposal-shape catalog grows).
- `compose()` chat-time signature — it just takes a new optional `sections` param.
- The Rule 9 / Rule 8 expectations on schema changes.

## 8. Rollout plan

| Day | Action |
|---|---|
| Backend day 1 | Migration 038 (ADD COLUMN, additive) + GET/PUT endpoints + `compose()` sections-aware path + tests. Lands behind flag default-OFF. |
| Backend day 2 | Migration 039 (`proposed_section_change` + `target_section_id` on coach_messages) + Coach META_PROMPT sections branch + `coach_reply` 5-tuple + `/apply` dispatch. |
| Backend day 3 | Bot-side opt-in: a one-shot script that splits an existing bot's flat `system_instructions` into 2-3 sections via Gemini, owner reviews on the Soul File page, taps "Use sections." |
| Mobile day 1+ | Builds the Soul File page against this contract. Mobile flag default-OFF until backend says go. |
| Cutover | Backend flips `COACH_SECTIONED_V2_ENABLED=true` globally; mobile flips their flag. Either side can flip back independently if alpha surfaces issues. |

## 9. Open questions for Rishi (none blocking — recommendations baked in)

1. **`previous_body_sha256` mandatory or optional?** Recommendation: mandatory on proposed_section_change; without it /apply has no way to detect concurrent drift. Mobile generates by hashing the body it saw when Coach proposed.

2. **Per-bot opt-in or global flag?** Recommendation: global flag for V1; per-bot column comes later if needed. Keeps the rollout reversible without a second migration.

3. **Bot-side opt-in script — auto-section by archetype or LLM-split?** Recommendation: LLM-split with creator review. Auto-section by archetype would put every "advisor" bot in identical sections, defeating the purpose.

4. **Max sections per bot.** Recommendation: 8. Above that the META_PROMPT's input-token budget on Gemini starts hurting; UI ergonomics also degrade.

5. **Bot version sha — bot-wide or sections-only?** Recommendation: sections-only for now. If mobile needs to detect concurrent edits to other parts of the bot (avatar, name, etc.), we add a bot-wide version column then.

## 10. Test surface (when Bucket 2 ships)

| Layer | What |
|---|---|
| Migration 038 source-pin | Column shape, default `[]`, idempotent |
| Migration 039 source-pin | Two new coach_messages columns, nullable |
| compose() behavioral | Sections render in L4 when flag ON; falls back to flat when flag OFF or sections empty; existing layers unchanged |
| GET /soul-file behavioral | Returns sections, version sha, fallback flag |
| PUT /soul-file behavioral | 200 on good, 409 on stale sha, 422 on bad shape, 403 on non-owner |
| Coach META_PROMPT source-pin | Sections-aware branch, rule list updated for sectioned mode |
| coach_reply return signature | 5-tuple, exactly one proposal kind set |
| /apply dispatch | proposed_section_change branch updates the right section, validates sha |
| section_hint flow | Carried into opening prompt when present, ignored when absent |
| Feature flag wiring | Default OFF; respects per-env override; doesn't crash if flag missing |

## 11. Mobile expert checklist before building

- [ ] Read this doc end-to-end.
- [ ] Reply with the 3 things you want flagged (sha approach, section_hint format, fallback rendering, etc.).
- [ ] Confirm the Soul File page mock matches the GET /soul-file response shape.
- [ ] Confirm Coach session can render the proposal-section badge from the new field.

Mobile work CAN start once you've signed off on this doc — backend lands behind a flag so mobile can iterate against a default-OFF backend; flip both on together when alpha is ready.

## Related

- PR-3 (#356 today) — typed lifecycle status that this builds on (every proposal kind goes through the same pending → applied flow)
- PR-1 (#348) / PR-2 (#349) / PR-4 (#350) / PR-5 (#351) — sibling Coach simplification PRs
- Plan §4 "Bucket 2 — section-as-unit" + Codex review §6 (section data model)
- `app/services/soul_file.py:172` (`compose`) — the function that grows the sections-aware path
- `app/services/coach.py:265` (`coach_reply`) — the 5-tuple change
- `app/routes/creator_coach.py:311` (`apply_coach_proposal`) — the dispatch site
