# Session 6 Execution Brief — Chat Quality & Observability (2026-06-26)

**From:** research/planning session. **To:** Session 6 (executor).
**Full analysis:** `docs/chat-quality-and-observability-recommendations-2026-06-26.md`.
**Review:** Codex reviewed + answered 2 follow-ups; resolutions baked in below.

These are **hypotheses for dispatch, not orders** — Rishi's preference is source of truth, and
nothing ships without his sign-off + the normal deploy pipeline (PR → CI → Codex → Rishi
"merge it" → deploy). One PR per concern, <400 lines, symmetry preserved.

## Locked decisions (do not re-litigate)
1. No quiet hours.
2. Skill check-ins **back off**, never hard-stop.
3. Background LLM stays on Saikat's `runpod_vllm`; **remove `internal_vllm` fallback** — but
   only **after** alerting exists (see task 4). User chat stays Gemini/OpenRouter.
4. Chat reader = Langfuse Sessions (`sessionId = conversation_id`).

---

## Execution order

Codex's senior sequence, with A1/A2 (user-facing bugs) elevated above penalty tuning.
Each task is independently shippable as its own PR.

### 1. B1 — Skill check-in backoff  *(stop the firehose)*
- **Where:** `app/services/proactive.py` — `send_skill_checkin` (~L391), cadence advance (~L510);
  `message_repo` for an unanswered-count helper.
- **What:** before sending / when computing next `next_event_at`, read unanswered skill-checkins
  and **multiply cadence by a backoff factor** (6h → 12h → 24h → 48h, cap ~weekly). Reset to base
  when the user replies. Per locked decision: slow down, do NOT hard-cap.
- **Accept:** a non-responding user's skill check-in interval visibly lengthens each round; a
  user reply resets it. **Size:** ~25 lines. **Risk:** low.

### 2. D — Langfuse Sessions wiring  *(Rishi can read chats)*
- **Where:** `app/services/langfuse_tracing.py:73` (trace `body`).
- **What:** add `"sessionId": conversation_id` to the trace body — **only when `conversation_id`
  is present** (conditional, per Codex; keeps non-chat traces clean).
- **Accept:** Langfuse Sessions tab groups a full user↔bot chat into one thread; new traces carry
  sessionId. **Size:** ~1-3 lines + redeploy. **Risk:** none.

### 3. C-L0 — Deterministic eval pass + per-reply storage  *(see problems automatically)*
- **What:** a no-LLM check on **every** reply: leak flags (`THINK`/`Constraint checklist`/
  `as an AI`), repetition (n-gram overlap vs the bot's last K replies), emoji count, length,
  ends-in-question. Persist **per-reply** records (text + flags + scores + outcome).
- **Deps (Codex):** needs a **new migration** (table + indexes + retention policy) — do NOT
  reuse the per-bot `bot_quality_scores` rollup (migration 013). New loop needs its **own
  kill-switch name** in `app/kill_switch.py` (don't reuse `quality_scorer`/`email_digest`).
- **Accept:** every new reply gets an L0 record; a daily count of leak/repetition flags is
  queryable. **Size:** moderate (loop + migration). **Risk:** low (read-only over outputs).

### 4. Alerting on runpod-primary failures  *(MUST precede task 9)*
- **Why (Codex):** callers fail soft today (`quality_scorer.py:72`, `memory.py:136`,
  `nudge.py:87`, skill checkin `proactive.py:379`) — removing the fallback would make outages
  **invisible** without this.
- **What:** alert (Sentry / registry failure-row count) when `runpod_vllm` primary fails for any
  background process. **Accept:** a simulated pod failure raises a visible alert. **Risk:** low.

### 5. A2 — In-character block fallback + NSFW-streaming fix
- **Resolved (Codex Q1 = option a):** keep the `BLOCKED_CONTENT` **error_code contract**
  (mobile keeps its icon/retry path, analytics still see a block, history isn't polluted).
  Replace the generic `ERROR_MESSAGES["BLOCKED_CONTENT"]` line with **archetype-aware text**.
- **Refinement (this session):** the archetype text must be **static canned templates** (one per
  archetype), NOT a second LLM generation — the refusal path must not make another blockable,
  latency-adding LLM call.
- **NSFW streaming (Codex = higher-quality bug):** the NSFW streaming path yields `NO_PROVIDER`
  ("temporarily unavailable") which misreads as an outage. Fix = transparently serve the reply
  **non-streaming server-side** (preferred; keep it backend-only — deliver the full reply as one
  SSE event so it's not a mobile change), or return a truthful `STREAMING_UNSUPPORTED` code.
- **Refinement (this session) — priority by measurement:** before sequencing NSFW-streaming vs
  the block-text fix, **count `NO_PROVIDER` vs `BLOCKED_CONTENT` occurrences** (logs/traces). Do
  the one that actually fires more first. Don't assume.
- **Where:** `app/services/ai_client.py` (block handling ~L440 + stream block path),
  `app/services/llm_types.py:16` (the canned text), NSFW-streaming branch in
  `generate_response_stream`. **Size:** ~30-50 lines. **Risk:** low-medium (contract-adjacent —
  verify mobile still reads `error.code`).

### 6. A3 — Anti-repetition penalties (registry/client layer)
- **Resolved (Codex Q2):** make `frequency_penalty` + `presence_penalty` **first-class params**,
  same status as `temperature`/`max_tokens`. Thread through `llm_registry.call()` /
  `call_stream()` → `_do_complete()` → encode per-provider:
  `openai_compatible.py` straight into the request body; `gemini.py` **inside `generationConfig`**
  (NOT top-level `extra_body` — avoids the clobber at `gemini.py:190`). `ai_client` only chooses
  values.
- **Refinement (this session) — value home:** put the per-archetype values in the existing
  `ARCHETYPE_TUNING` dict (`soul_file.py:173`, which already carries `temperature`/`max_tokens`),
  with a global `config` default (`FREQUENCY_PENALTY`/`PRESENCE_PENALTY`, start ~0.3/0.3,
  env-tunable live).
- **Accept:** a chat request carries the penalties to both providers, encoded correctly;
  repetition drops measurably in L0 (opening-phrase reuse). **Size:** moderate (registry + 2
  clients + tuning). **Risk:** low (reversible via env).

### 7. A4 — Soften the forced reply-hook
- **Where:** `soul_file.py:67-70` — move the "end with a hook" rule from `GLOBAL_RULES_FIXED`
  into `GLOBAL_RULES_OVERRIDEABLE`, reworded to "vary how you end — a question, an observation,
  or an evocative beat; don't end every message the same way."
- **Note:** the rule encourages *hooks*, not literally questions (the 57%-`?` is model behavior).
  Phase 12 lesson (`soul_file.py:115`): this is variety guidance, NOT a length cap — safer, but
  **measure before/after via L0/L1.** **Size:** ~10 lines. **Risk:** low (measure).

### 8. A1 — Scaffolding-leak sanitizer + offending-bot cleanup
- **What (defense):** strip `THINK` / `**Plan for the response:**` / `**Constraint checklist…**`
  / `This response is:` from output before save/stream, in `ai_client.py` (both paths).
- **Codex constraint:** in the **streaming** path do NOT regex per token chunk — use a small
  **streaming filter / state machine** (mirror the existing stream-filter pattern) so scaffolding
  split across chunk boundaries can't leak partial text.
- **What (root cause):** grep `ai_influencers.system_instructions` for these phrases (the
  astrology bot especially) and clean the offending bot prompts — likely creator-pasted templates.
- **Accept:** L0 leak-flag count drops to ~0; no scaffolding in sampled outputs. **Size:**
  sanitizer ~40 lines + a data fix. **Risk:** low-medium (streaming correctness — test boundaries).

### 9. Fallback removal  *(only after task 4)*
- **What:** remove `fallback_provider`/`fallback_model` from the 6 background processes in
  `llm_registry.py:299` (`proactive_generation`, `quality_scorer`, `memory_extraction`,
  `memory_consolidation`, `nudge_generation`, `video_idea_generation`) → Saikat-or-fail-loud.
- **Accept:** with alerting live (task 4), a pod failure is visible, not silent. **Risk:** low
  once task 4 lands; do NOT do this before it.

---

## Later (design projects — NOT first pass)
- **A7** opening-gambit redesign (the real 100X on the ≤2-message bounce).
- **B2-B4** shared cross-channel unanswered budget · engagement-tiered cadence · eligibility gate.
- **C-L1** cheap runpod judge on last-24h with context + new rubric + daily digest → then
  **C-L2** strong-judge calibration → **C-L3** ~5-min/day golden-set ritual (Rishi's daily-
  minutes TBD).

## Open item for Rishi
Eval L3 ritual minutes/day (default 10 labels ≈ 5 min). If zero, lean on C-L2 for calibration.
