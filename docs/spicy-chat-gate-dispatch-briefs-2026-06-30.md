# Spicy chat gate — dispatch briefs (2026-06-30)

Design is locked (20 decisions). Full design: `docs/spicy-chat-gate-design-2026-06-28.md`.
Two paste-ready briefs below: **(1) Session 6 master dispatch**, **(2) new amorae.ai Web Session spawn**.

---

## BRIEF 1 — Session 6 (orchestrate; immediate ask = spawn the web session)

> **Spicy chat gate — over to you to orchestrate. Design is 100% locked (20 decisions) at `docs/spicy-chat-gate-design-2026-06-28.md`; the build tracks + the web-session spawn brief are in `docs/spicy-chat-gate-dispatch-briefs-2026-06-30.md` (Brief 2). Read both.**
>
> **Immediate ask:** spawn the new **amorae.ai Web Session** in a new terminal, using "Brief 2 — amorae.ai Web Session spawn" verbatim from the dispatch-briefs doc. It owns the `amorae.ai` web front-end + its own backend + `amorae_db`.
>
> **The rest is yours to orchestrate — decide what to do and when.** Full spec is in the design doc; in brief: brand `amorae.ai` (Namecheap); hosting on rishi-4/5/6 with its OWN `amorae_db` (Level 2 — adult messages never touch `yral_agent_db`); prompt-driven deflection + existing content-safety filter as the app-surface backstop; valet-ticket auth handoff; free text-only v1; geo-gate default-open; launch-everywhere-then-geo-restrict. You decide how/when to dispatch the v2 backend (dev session), infra (DNS/SSL + `amorae_db`), and mobile (later — flag-gated, post-Motorola), and to kick off the India legal read in parallel. Walking-skeleton first (app deflection → `amorae.ai/tara` → 18+ gate → bare text chat via ticket handoff) is the suggested approach; sequence it however you judge best.
>
> **Guardrails that still bind:** normal pipeline (PR → CI + Codex → Rishi "merge it" → deploy); pg_dump before migration 045; never touch chat-ai routes on 1/2/3 (Rule 7); adult messages never touch `yral_agent_db`; mobile flag `SpicyChatGateEnabled` `defaultValue=false` + no mobile PR before Rishi's Motorola pass; web brand live before the native SFW-constraint flips (Risk 4); don't touch the #424 NSFW streaming path; distinct branch names per session.

### Reference — the build tracks (for Session 6 to dispatch as it sees fit)

- **Infra:** `amorae.ai` DNS + SSL via own per-service `.caddy` site; create `amorae_db` on Patroni.
- **v2 backend (dev session), small single-concern PRs:** (1) migration `045_user_nsfw_consent` (additive, pg_dump first); (2) consent endpoints `POST`/`GET /api/v1/users/nsfw-consent`; (3) auth handoff `POST /api/v1/spicy/handoff` (mint 60s single-use Redis ticket) + `/spicy/handoff/exchange`; (4) native deflection branch (prompt-driven SFW persona surfacing `spicy_landing_url` + content_safety filter re-enabled on app surface as backstop; don't touch #424); (5) server-enforced `surface` flag (`app` vs `web_spicy`); (6) expose `is_nsfw` + `spicy_landing_url` on influencer list+detail; (7) context-read endpoint + optional still-active-ping receiver; (8) geo-gate capability, default OPEN.
- **Web brand (NEW amorae.ai Web Session):** per Brief 2.
- **Mobile (LATER):** tame "Chat with me →" link + in-chat CTA card behind `SpicyChatGateEnabled=false`; waits on deflection message format + Sarvesh `ChatMessageDto` alignment; no PR before Rishi Motorola pass.
- **Parallel (non-eng):** India legal read.

---

## BRIEF 2 — new "amorae.ai Web Session" spawn

> **You are the amorae.ai Web Session.** You own a NEW web property — `amorae.ai`, the "spicy" adult-chat surface for the YRAL AI influencer Tara — its front-end, its own backend service, and its own database `amorae_db`. You are separate from the dev session (Python/v2 backend) and the mobile expert (Kotlin).
>
> **Working dir / context — READ FIRST:** `/Users/rishichadha/Claude Projects/yral-rishi-agent/docs/spicy-chat-gate-design-2026-06-28.md` — the WHOLE doc, especially §1 (Option A architecture), §3 (UX flow), §4.2 (your own backend + DB), §4.4 (Level 2), §4.7 (auth handoff), §4.8 (background services), and the Visual architecture section. Also `CLAUDE.md` for fleet + deploy rules.
>
> **Why this exists:** NSFW chat can't live in the YRAL app (App/Play Store bans). The app keeps Tara SFW and, when a user pushes for explicit content, she surfaces a link to `amorae.ai/tara`. Your surface is where she chats freely. This isolates adult content on a website the app stores don't review.
>
> **Build v1 = a "walking skeleton" first (end-to-end, ugly is fine), then polish:**
> 1. **Landing page** `amorae.ai/{bot_handle}` — Linkme/OnlyFans-style *pattern* (hero image, name + handle, "Mature Content Disclaimer → **Continue (18+)**" card, footer **Privacy | Terms | Report**). **Own brand identity — do NOT copy OnlyFans/Linkme logos, name, or exact styling (trademark risk).**
> 2. **18+ gate** — "Continue (18+)" sets your OWN httpOnly cookie (the live gate, ~90d TTL); for logged-in users call v2 to write the per-account consent audit row.
> 3. **Auth handoff (no re-login)** — receive `?t=<ticket>`, call v2 `POST /api/v1/spicy/handoff/exchange` to resolve identity, set your own session cookie. NEVER accept/expect a raw JWT in the URL. Anonymous users may view the landing; require login only at "Continue (18+)".
> 4. **Web chat** — TEXT ONLY v1, FREE (no billing). Unconstrained NSFW persona. Reuse the same LLM model/provider as v2's `user_chat_main_nsfw` (OpenRouter) — via a shared routing lib or direct call. **Persist all messages to `amorae_db` (your OWN database) — NEVER to `yral_agent_db`.**
> 5. **Context seeding** — at session start, one-time READ of the user's recent SFW app messages via v2's context-read endpoint so Tara "remembers." Read-only; you only WRITE adult replies into `amorae_db`.
> 6. **Report / Privacy / Terms** pages — required (Google AI-content policy + Apple 1.2). The report affordance lives here on the web.
> 7. **Geo-gate** — server-side region check, **default OPEN** (config flip to restrict later).
>
> **Hard constraints:**
> - Hosted on the rishi-4/5/6 cluster; deploy via your own per-service `.caddy` site (same pattern as other services; never touch chat-ai routes on 1/2/3 — Rule 7).
> - Your DB is `amorae_db` on the same Patroni. Adult messages NEVER touch `yral_agent_db`. You have no write access to it.
> - Text-only, free, for v1. Voice = later fast-follow; images = separate later decision (moderation/legal).
> - Coordinate the API contract (handoff/exchange, context-read, still-active ping) with the dev session — don't invent endpoints unilaterally.
> - Pipeline: feature branches with distinct names, PR → CI → Rishi approval → deploy. Never push to main. pg_dump-style care for `amorae_db` schema.
>
> **First deliverable:** the walking skeleton — `amorae.ai/tara` landing → Continue (18+) → a working (unstyled) text chat with Tara, authenticated via the ticket handoff, persisting to `amorae_db`. Then iterate on the Linkme/OF-style polish.
