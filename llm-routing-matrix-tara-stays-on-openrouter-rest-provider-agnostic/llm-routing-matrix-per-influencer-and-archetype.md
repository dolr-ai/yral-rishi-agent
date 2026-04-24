# LLM Routing Matrix — preserving OpenRouter for Tara

**Rishi's rule (2026-04-23):** Tara is the best bot we've built. She currently runs on OpenRouter — keep her there in v2. Other bots can be Gemini or agnostic (my judgment).

## The routing strategy

The orchestrator talks to an abstraction: `llm_client.chat(messages, model=None)`. Under the hood, this is a simple dispatch table that picks a provider based on (influencer_id, archetype, turn_type). Config-driven via the Soul File layers + a routing config table. Changing routing = one config update; no code change.

### Default routing table

| Bot / archetype / turn type | Provider | Model | Why |
|---|---|---|---|
| **Tara (specific influencer_id, whatever that is today)** | **OpenRouter** | **same model Tara uses today** (read from yral-chat-ai config) | **Rishi's explicit ask 2026-04-23 — she's the best we have, don't touch her** |
| Companion archetype (default, most influencers) | Gemini | `gemini-2.5-flash` | Fastest TTF, cheapest, proven at our current latency baseline |
| Nutritionist / coach / instructor / specialist archetypes | Gemini | `gemini-2.5-flash` | Same baseline; swap to Claude if we need deeper reasoning |
| Crisis-detected turn (content-safety flags it) | Claude | `claude-opus-4-7` | Deeper reasoning + better emotional calibration for mental-health-adjacent conversations |
| Creative roleplay / storytelling influencers | Claude | `claude-sonnet-4-6` | Better prose + character consistency |
| NSFW-flagged influencers (already route here today) | OpenRouter | whatever OpenRouter model we use today | Preserved from yral-chat-ai behavior |
| Soul File Coach (creator meta-chat) | Claude | `claude-sonnet-4-6` | Meta-reasoning about prompts; Claude is better at this |
| Memory extraction background worker | Gemini | `gemini-2.5-flash` | Bulk async; cheapest model that does the job |
| Eval-harness LLM-as-judge | Claude | `claude-sonnet-4-6` | Highest quality judging |

### Provider selection priority

Per-turn provider chosen by this order (first match wins):
1. **Influencer-specific override** (e.g., Tara → OpenRouter). Set via Soul File Layer 3 (per-influencer).
2. **Archetype override** (e.g., crisis detected → Claude Opus). Set via Soul File Layer 2 (archetype).
3. **Turn-type default** (streaming chat → Gemini Flash, reasoning → Claude). Global Layer 1 default.
4. **Fallback** (Gemini Flash) if nothing matches.

Config table lives in Postgres (`agent_llm_routing` schema):
```sql
CREATE TABLE llm_routing_rule (
    rule_id UUID PRIMARY KEY,
    match_influencer_id VARCHAR(255),      -- NULL = match any
    match_archetype VARCHAR(64),            -- NULL = match any
    match_turn_type VARCHAR(64),            -- NULL = match any
    provider VARCHAR(32) NOT NULL,          -- gemini, claude, openrouter, self-hosted
    model_name VARCHAR(128) NOT NULL,       -- e.g., "gemini-2.5-flash"
    priority INT NOT NULL,                  -- lower number = higher priority
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_llm_routing_rule_priority ON llm_routing_rule(priority, enabled);
```

### The Tara rule as an explicit config row

```sql
INSERT INTO llm_routing_rule VALUES (
    gen_random_uuid(),
    '<tara_influencer_id>',    -- match ONLY Tara
    NULL,                       -- any archetype
    NULL,                       -- any turn type
    'openrouter',
    '<tara_openrouter_model>',  -- preserved from yral-chat-ai
    0,                          -- highest priority
    TRUE,
    NOW(), NOW()
);
```

## Cost-observability per turn

Per Rishi's "no cost controls until PMF, but runaway-protection cap" rule:
- Every LLM call's cost (estimated from token count × per-model rate) logged to Langfuse
- Per-user daily cost tracked in Redis → summarized in analytics
- Runaway-protection: if a single user exceeds ₹500/day (very high, 10-100× normal), route to cheapest model for remaining turns + Sentry warn
- NOT a unit-economics gate — just abuse-protection

## Provider failover

If a provider is slow or erroring:
- Gemini TTF >500ms for 5 consecutive turns → auto-switch to Claude Haiku fallback for next N turns, page Rishi
- OpenRouter errors for Tara → fallback to a pre-configured Claude model for that turn, Sentry warn
- All failover logic in llm-client abstraction; orchestrator unaware

## What we need to look up

Before freezing this config, I need to check yral-chat-ai's current `ai.rs` or `ai.py` to find:
1. Which exact OpenRouter model Tara uses today
2. Whether Tara is identified by `influencer_id` or by personality trait (if trait-based, we need a different routing rule)
3. What OpenRouter config lives in env vars today (API key, base URL)

This lookup happens in Phase 0 as part of the feature-parity audit + latency baseline capture. Until then, the routing table above is the plan; actual values locked during implementation.

## Self-hosted LLM track (deferred per Rishi 2026-04-23)

The llm-client abstraction supports a `self-hosted` provider option. When GPU capacity is available from Saikat, we add a routing rule that directs some archetypes to self-hosted. Not Tara (Rishi explicit — she stays on OpenRouter). Probably start with memory-extraction background worker (bulk, async, not latency-critical). Gates: self-hosted model must match or beat Gemini's TTF for interactive turns, or be reserved for non-interactive ones.
