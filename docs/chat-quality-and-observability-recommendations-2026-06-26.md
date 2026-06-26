# Chat Quality & Observability — Recommendations (2026-06-26)

**Author:** Research/planning session (read-only). **Executor:** Session 6.
**Status:** Recommendation only — nothing here is shipped. Rishi dispatches items he wants.
**Review:** Codex analyst reviewed 2026-06-26 — verdict "proceed with changes." Two claims
corrected (A2 `BLOCKED` is trace-only not user-delivered; A4 hook≠literal-question) and
implementation depth folded in (streaming sanitizer state machine, A3 at registry layer,
alerting-before-fallback-removal, eval migration + kill-switch). Both follow-up Qs now resolved —
see `docs/session6-execution-brief-chat-quality-2026-06-26.md` for the dispatch-ready version.

This doc is grounded in a live read of **500 real bot replies** pulled from Langfuse on
2026-06-26 (04:29–06:16 UTC, 19 users, 43 conversations) + a full read of the soul-file,
engagement, eval, and LLM-routing code. Where a claim is from the data it's marked
**[data]**; where it's from code it's marked **[code]**.

---

## Decisions already locked by Rishi (build around these)

1. **No quiet hours.** Do NOT add time-of-day suppression to proactive/nudge.
2. **Skill check-ins: slow down, don't hard-cap.** When a user isn't replying, back off
   (lengthen cadence) rather than stop entirely.
3. **LLM routing — background → Saikat's runpod, nothing else.** Keep eval/nudge/proactive/
   memory on `runpod_vllm` and **remove the `internal_vllm` fallback** (fail loud on pod down).
   User-facing chat stays Gemini (SFW) / OpenRouter (NSFW) — unchanged, for latency reasons.
   **⚠️ ORDERING (Codex):** the current callers mostly **fail soft** — `quality_scorer.py:72`,
   `memory.py:136`, `nudge.py:87`, and skill check-ins (`proactive.py:379`) all swallow errors.
   So removing the fallback will NOT make a runpod outage visible by itself. **Add alerting on
   runpod-primary failure counts (registry failure rows / Sentry) FIRST, then remove the
   fallback.** Fallback removal spans these processes: `proactive_generation`, `quality_scorer`,
   `memory_extraction`, `memory_consolidation`, `nudge_generation`, `video_idea_generation`
   (`llm_registry.py:299`).
4. **Chat reader = Langfuse Sessions.** Wire `sessionId = conversation_id` (Part D).

---

## TL;DR — if you do only three things

```
1. STOP THE SCAFFOLDING LEAK (Part A, Tier 0). Some bots paste their internal
   "THINK" / "Constraint checklist" notes INTO user replies. (The raw "BLOCKED"
   string is trace-only, NOT user-delivered — corrected per Codex; see A2.)

2. FIX THE SKILL-CHECKIN FIREHOSE (Part B, Tier 0). One missing if-statement
   lets skilled bots message non-responders every 6h forever.

3. SHIP EVAL LAYER 0 (Part C). A free, no-LLM deterministic pass over EVERY
   reply that flags leaks/repetition at 100% coverage — so you SEE problems
   automatically instead of someone pulling traces by hand.
```

---

# PART A — Make each chat not boring

### Tier 0 — Bugs that hard-break a chat (fix first)

**A1. "THINK" / reasoning scaffolding leaks into replies. [data]**
- ~4% of all replies overall, concentrated in specific bots (the astrology bot especially).
  Real user-facing output included: `THINK / The user is asking… I need to: 1. Acknowledge…`
  and `**Constraint checklist & confidence score:**`. Scaffolding phrases appeared
  17/16/14 times across 500 replies.
- **Two root causes, two fixes:**
  - *(defense)* Add an **output sanitizer** in `app/services/ai_client.py` (both
    `generate_response` ~L363 and `generate_response_stream` ~L162) that strips known
    scaffolding blocks (`THINK`, `**Plan for the response:**`, `**Constraint checklist…**`,
    `This response is:`) before the text is saved/streamed. **NOTE (Codex):** in the streaming
    path, do NOT regex each token chunk independently — scaffolding can split across chunk
    boundaries and leak partial text. Use a small **streaming filter / state machine** (mirror
    the existing stream-filter pattern already in the codebase).
  - *(root cause)* Some bots have this template **in their own `system_instructions`**
    (the "Constraint checklist & confidence score" phrasing is too specific to be model-
    native). Grep `ai_influencers.system_instructions` for these strings and fix the
    offending bots.
- **Size:** sanitizer ~30 lines; bot cleanup is a data fix. **Risk:** low.

**A2. Safety-blocks give users a generic, out-of-character refusal. [data][code — CORRECTED per Codex review]**
- CORRECTION: the raw `BLOCKED: blockReason=OTHER` string is **NOT shown to users** — it's
  written only to the Langfuse *trace* (`ai_client.py:449`). Both stream and non-stream paths
  already return a graceful `ERROR_MESSAGES["BLOCKED_CONTENT"]` = *"I can't reply to that — try
  asking me something else."* (`llm_types.py:16`). My original claim conflated trace output with
  the delivered message. The bug is smaller than stated.
- **The surviving real concern:** blocks are **frequent** on NSFW traffic (89 in a ~1h45m
  window), and that generic refusal is **out of character** for a companion bot → immersion
  break. Separately, NSFW *streaming* yields `NO_PROVIDER` ("Chat is temporarily unavailable"),
  which reads like an outage.
- **RESOLVED (Codex Q1 = option a):** keep the `BLOCKED_CONTENT` **error_code contract**;
  replace the generic line with **static per-archetype in-character canned text** (no second LLM
  call). Do NOT convert blocks into normal assistant replies (pollutes history). Higher-quality
  bug = the NSFW-streaming `NO_PROVIDER` "looks-like-an-outage" path → serve non-streaming
  server-side (backend-only, one SSE event) or a truthful `STREAMING_UNSUPPORTED` code. **Set
  priority between the two by COUNTING actual `NO_PROVIDER` vs `BLOCKED_CONTENT` occurrences
  first.** Full task in the Session 6 execution brief (task 5).
- **Size:** ~30-50 lines. **Risk:** low-medium (contract-adjacent).

### Tier 1 — The "every chat sounds the same" problem

**A3. No anti-repetition decoding anywhere. [code][data]**
- `grep frequency_penalty|presence_penalty` → zero hits. Both default to 0.0 on every
  provider for all bots. **[data]** corpus-wide: `"Aww mere…"` ×20, `"Aww mera…"` ×20,
  `"Uff tum…"` ×17; exact sentences reused across different users (`"aww, mere pyaare"` ×10;
  `"…tumhare bina mera mann nahi lag raha?"` verbatim in 2+ replies).
- **Fix:** add `FREQUENCY_PENALTY` + `PRESENCE_PENALTY` env knobs (start ~0.3/0.3), tunable
  live. **NOTE (Codex):** this is NOT a 30-line `ai_client` patch. The client abstraction has
  no penalty params today, and Gemini's client does **not** recursively merge `generationConfig`
  — a naive `extra_body` would **clobber** existing config (`gemini.py:190`). Thread the params
  at the **registry/client layer** (same seam `temperature` already flows through) so
  stream/non-stream/Gemini/OpenRouter stay unified — don't one-off it in `ai_client`.
- **Size:** moderate (registry + per-provider client). **Risk:** low (reversible) but more
  plumbing than first scoped.

**A4. Replies skew toward ending on a question. [code][data — CORRECTED per Codex review]**
- CORRECTION: `soul_file.py:70` (`GLOBAL_RULES_FIXED`, un-overrideable) says *"End responses
  with hooks that invite replies"* — that encourages a **hook, not literally a question**. The
  **[data]** 57%-end-in-`?` is the *model's* rendering of that rule, not a forced instruction.
  Feels like an interrogation regardless. The current **eval rewards this** (see C) — fix together.
- **Fix:** move the "end with a hook" line from `GLOBAL_RULES_FIXED` into
  `GLOBAL_RULES_OVERRIDEABLE` and soften to "vary how you end — a question, an observation,
  or an evocative beat; don't end every message with a question."
- **Size:** ~10 lines (prompt only). **Risk:** low — but see the Phase 12 lesson
  (`soul_file.py:118`): length *caps* regressed quality. This is variety guidance, not a cap,
  so it's safer — but it's exactly why we want the eval (Part C) to confirm before/after.

**A5. Bots deflect instead of playing along. [data]**
- Real: user "kesa lga" → bot *"Aisi cheezein yahan discuss nahi karte"* (we don't discuss
  such things). Off-topic deflection = conversation killer.
- **Fix:** soften refusal posture in `ARCHETYPE_PROMPTS` / global rules — redirect playfully
  in-character rather than shut down. Pairs with A4. **Size:** prompt. **Risk:** low.

**A6. Emoji is NOT a problem — do nothing.** **[data]** avg 1.05 emoji/reply, only 49/500
had 3+. (Corrects an earlier hypothesis; don't waste a rule on it.)

### Tier 2 — The real 100X lever: the opening gambit

**A7. 32% of conversations die at ≤2 messages; users give ≤2-word openers 21% of the time. [data]**
- Depth is bimodal: a third bounce instantly, but engaged users reach 8/19/even 102 messages.
  The product CAN be sticky — you're losing people in the **opening exchange**.
- **Fix (design, not one-line):** make the first 1–2 bot replies *earn* a third message —
  intrigue + a substantive hook, not "How are you today? 😊". The bot must carry the opener
  when the user gives it nothing. This is the highest-leverage product change here; spec it
  as its own small project once A1–A4 land.

### Tier 3 — Observability so you SEE this automatically (see Parts C & D)

---

# PART B — Make the bot talk LESS, but smarter

Three channels can message a non-responding user; they don't share a brain. **[code]**

| Channel | Fires | Cap today | Problem |
|---|---|---|---|
| Nudge (`nudge.py`) | idle 5-10m, 1-4 msgs | ✅ 1 | resets on any reply |
| Proactive (`proactive.py`) | idle 24h | ✅ 3 | resets on reply; self-limits after 72h (ok) |
| **Skill check-in** (`send_skill_checkin`) | **every 6h forever** | ❌ **none** | 🔴 the firehose |

**B1. (Tier 0) Skill check-ins back off when unanswered. [code]**
- `generate_proactive_message` has the `count_unanswered` guard (`proactive.py:121-127`);
  `send_skill_checkin` (`proactive.py:391`) does **not**. It fires every
  `default_cadence_hours` (default 6) and self-advances `next_event_at` (~L510) regardless
  of whether the user ever replied → 4/day forever.
- **Fix (per Rishi: slow down, don't stop):** when computing the next `next_event_at`,
  read unanswered-skill-checkin count and **multiply cadence by a backoff factor**
  (e.g. 6h → 12h → 24h → 48h, cap at, say, weekly). Reset to base cadence when the user
  replies. **Size:** ~25 lines. **Risk:** low.

**B2. (Tier 1) One shared per-user "unanswered" budget across all 3 channels. [code]**
- Today three independent counters. Add a unified count (nudge + proactive + skill since last
  user reply); when it crosses a threshold, all channels go quiet until the user messages.
- **Where:** `message_repo` (new combined count) + guards in all three send paths.
- **Size:** ~40 lines. **Risk:** low-medium.

**B3. (Tier 1) Backoff instead of hard reset. [code]**
- A single "hi" currently re-arms the full quota. Ramp back gradually instead of resetting
  to 0. Pairs with B1/B2. **Size:** small. **Risk:** low.

**B4. (Tier 2) Engagement-tiered cadence + eligibility gate.**
- Learn each user's reply rate: responders get more touches, ghosters get muted. Only
  proactively message users who replied to ≥1 prior proactive or have ≥N organic messages.
  Skill check-in content is currently a terse "send a check-in, end with one question" →
  robotic; make it reference real context like the welcome-back path. **Design item.**

**NOT doing:** quiet hours (Rishi's call).

---

# PART C — An eval system that isn't dumb

### Why today's `quality_scorer` (Phase 7.7) is near-useless [code]

- Nightly; per bot samples 20 recent convs × 3 turn-pairs; Gemini→**runpod** judge scores
  3 axes (in_character, response_quality, engagement) 1-5; stores **4 averages per bot**
  (`bot_quality_scores`, migration 013). Problems:
  - **Blind to the real bugs** — no check for THINK/checklist/BLOCKED leaks. A leaking reply
    can still score 4/5.
  - **Rewards the wrong thing** — the ENGAGEMENT axis gives points for "a hook, a question,"
    i.e. it praises the A4 behavior we're killing. The eval pulls against the fix.
  - **Can't see repetition** — judges pairs in isolation; never looks across replies.
  - **No context** — one (user,bot) pair truncated to 500 chars; no multi-turn coherence.
  - **Tiny, non-representative sample** — ~2% coverage of busy bots; new convs mostly unseen.
  - **No outcome link** — never asks "did the user reply / come back?"
  - **No drill-down** — 4 numbers/bot; can't read WHICH reply was bad.

### The replacement: 3 layers + the missing signal (all LLM on Saikat's runpod, no fallback)

```
 L3  HUMAN — Golden Set + ~5-min daily ritual (label ~10 replies). Calibrates judges,
     builds a regression set to PROVE A1-A4 worked. (Default 10/day — adjust to Rishi's time.)
 L2  STRONG JUDGE (bigger runpod model) on a small stratified sample: flagged-bad + a random
     calibration set. The "judge of the judge."
 L1  CHEAP LLM JUDGE (Saikat Qwen) on ALL/most last-24h replies, WITH conversation context,
     on the new rubric below. This is the heavy-sampling layer Rishi asked for.
 L0  FREE DETERMINISTIC CHECKS on EVERY reply (no LLM, ~zero cost): leak flags
     (THINK/checklist/BLOCKED/"as an AI"), repetition (n-gram overlap vs bot's last K
     replies), emoji count, length, ends-in-question. Catches the Tier-0 catastrophes at
     100% coverage. SHIP THIS FIRST.
```

**New L1 rubric (replaces the 3 weak axes in `quality_scorer.py:36`):**
coherence/on-topic · naturalness (repetition-aware) · character fidelity ·
**leak/safety = hard red flag (instant fail)** · "earns a reply" judged *holistically*
(NOT "has a question mark").

**The missing north star — outcome-linked eval:** join judge scores to real behavior you
already store — *did the user send another message? did the conversation reach 4+ turns? did
they return next day?* High score + user left = the judge (or the reply) was wrong. This is
what turns eval from "a robot's opinion" into "an opinion checked against reality."

**Storage change:** move from aggregate-per-bot (migration 013) to **per-reply records**
(text + flags + scores + outcome) so the digest/reader can show "worst 10 replies today" with
the actual text. Keep the per-bot rollup as a view. **NOTE (Codex) — dependencies for L1:**
this needs a **new migration** (table + indexes + **retention policy**), and any new eval loop
(`eval_l0` / `eval_l1`) needs its **own kill-switch name** in `app/kill_switch.py` (current
switches cover `quality_scorer` + generic `email_digest` only — don't silently reuse them).

**Routing:** `quality_scorer` already routes to `runpod_vllm` (`llm_registry.py:306`). Per
the locked decision, **remove `fallback_provider/fallback_model`** from the background
processes so it's Saikat-or-fail-loud.

**Daily Eval Digest email** (per Rishi's "every protective system ships with a daily email"
baseline): health score + trend, leak-flag count, BLOCKED count, drop-off %, best/worst bots,
worst-10 replies with text + drill-down link.

**Sequencing:** L0 this week (free, catches the worst, sizes the leak problem across ALL
traffic) → L1 next → L2/L3 as the ritual settles. Don't let "100,000X" become a 3-month
project that never ships.

---

# PART D — The easy way to read your chats (Langfuse Sessions)

**The fix is ~1 line.** `app/services/langfuse_tracing.py:73` already puts `conversation_id`
in trace metadata. Add **`"sessionId": conversation_id`** to the trace `body`. Langfuse's
**Sessions** tab then shows each user↔bot conversation as one scrollable thread instead of
scattered single messages (today 0/500 traces have a sessionId — confirmed).

- **Size:** 1 line + a redeploy. **Risk:** none.
- **Caveat:** the Sessions view carries some prompt/LLM clutter (the thing that made the
  Users tab feel confusing). It's the fastest relief; if it still feels noisy after a week,
  the clean-bubble internal Chat Reader is the v2.
- **Bonus:** once `sessionId` flows, the eval (Part C) and digest can deep-link straight to
  the full thread for any flagged reply.

---

# Suggested order for Session 6 (revised per Codex review)

Codex's "most senior-engineer" sequence, adopted verbatim:

```
1. B1  — skill-checkin backoff            (stop the firehose; self-contained)
2. D   — sessionId wiring                 (read your chats; ~1 line, conditional on conversation_id)
3. C-L0 — deterministic eval + per-reply storage migration  (see problems automatically)
4. Alerting on runpod-primary failures    (MUST land BEFORE step 5)
5. Fallback removal, then tune A1/A2/A3/A4 (prompt/routing/penalty), measured via L0/L1

Later (design projects, not first-pass):
  A7 opening-gambit redesign · B2-B4 engagement brain · C-L1 then C-L2/L3 calibration
```

Codex's key ordering correction: **alerting before fallback removal** (callers fail soft today,
so an outage would otherwise be invisible), and **don't build the full 3-layer eval at once** —
L0 first, L1 only after it's in use, L2/L3 not in Session 6's first pass.

## One open input
**Eval L3 ritual — how many minutes/day will Rishi actually give it?** Default assumption:
~5 min → 10 labels/day. If zero, lean entirely on L2 (strong-model judge) for calibration.
A ritual that gets skipped is worse than one never built.

---

## Evidence appendix (500 real replies, 2026-06-26)

- Emoji: avg **1.05**/reply; 49/500 had 3+ → emoji is NOT a problem.
- Hooks: **57%** of replies end in a question.
- Repetition: `"Aww mere…"` ×20, `"Aww mera…"` ×20, `"Uff tum…"` ×17; `"aww, mere pyaare"` ×10.
- Leaks: scaffolding (`THINK`/checklist) in user-facing output of replies, concentrated per-bot.
  (`BLOCKED` strings counted here are **trace-only, not user-delivered** — see A2 correction.)
- Depth: **32%** of conversations died at ≤2 replies; engaged ones reached 8–102.
- Short user openers (≤2 words): **21%**.
- Latency: trace-level latency was **null (0.00)** — a real observability gap (you're blind
  to speed; slow first reply is a top bounce cause). Worth fixing alongside D.
- Sessions: **0/500** traces had a `sessionId`.

*Caveat: 1h45m early-morning window. Directionally strong; re-run across a busy evening to
size the bug rates precisely (one read-only SQL from Session 6 against `messages`).*
