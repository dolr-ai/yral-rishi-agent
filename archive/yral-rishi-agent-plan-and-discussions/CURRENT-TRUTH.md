# 🎯 CURRENT TRUTH — single source of agreement

> **Purpose:** when docs disagree, this file says what's actually true. Created 2026-04-27 in response to Codex audit that flagged doc drift between README, CONSTRAINTS, TIMELINE, and the priority doc. Coordinator updates this file whenever a binding decision changes.
>
> **Authority chain when conflicts exist:** `CONSTRAINTS.md` > this file > priority doc > TIMELINE > README. CONSTRAINTS always wins.

---

## ⏯️ Build mode (right now)

```
   STATE:   PLAN-ONLY
   TRIGGER: Rishi types "build" in coordinator session
   THEN:    Authorized scope per A13 (local laptop + mobile per A12)
            Cluster deploy still needs separate explicit YES (Day 4-7)
            Caddy snippet on rishi-1/2 still needs separate explicit YES (Day 7)
   NEVER:   yral-mobile push (A12); cutover (A6); deletion (A1);
            live chat-ai DB pull without per-op YES (A14)
```

Source: CONSTRAINTS A5, A13, I1.

---

## 📱 Mobile URL (right now and through cutover)

```
   TODAY (chat-ai live):        chat-ai.rishi.yral.com
                                 (mobile prod app hits this)

   DURING V2 BUILD (Day 8+):    agent.rishi.yral.com
                                 (debug APK only — locally hardcoded
                                  per A15; never pushed to origin per A12)

   AT CUTOVER (Rishi's call):    chat-ai.rishi.yral.com
                                 (Caddy on rishi-1/2 starts upstreaming
                                  some/all traffic to rishi-4/5; mobile
                                  URL stays the same — A3 one-change rule)
```

Source: CONSTRAINTS A3, A9, A15, A16.

---

## 💰 Paywall contract (corrected, single source)

```
   CONTRACT: pre-chat access check via yral-billing
   PROTOCOL: ApiResponse<ChatAccessDataDto{hasAccess, expiresAt}>
   NOT a 402 HTTP response. NEVER WAS.

   FLOW:
     1. Mobile (or v2 public-api on mobile's behalf) calls yral-billing
        access-check endpoint BEFORE sending a chat message
     2. If hasAccess=true → mobile sends the chat message
     3. If hasAccess=false → mobile triggers Google Play IAP sheet
        client-side; mobile POSTs purchase_token to /google/chat-access/grant
     4. yral-billing verifies, inserts bot_chat_access row, status flips to Active
     5. Next chat message succeeds

   V2's role: cache yral-billing's access decision in Redis 60s TTL.
              Match the exact ApiResponse envelope shape.
              Do NOT return 402 anywhere.
```

Source: CONSTRAINTS E7, README Section 11.8.2, `reference_yral_auth_billing_architecture.md` memory. **Codex audit 2026-04-27 caught lingering 402 references in README — now fixed.**

---

## 🛣️ Routing path (Day 8+, the real cluster shape)

```
   Mobile debug APK
        │
        │  HTTPS POST agent.rishi.yral.com/...
        ▼
   Cloudflare DNS (existing wildcard *.rishi.yral.com)
        │
        ▼
   rishi-1 + rishi-2 (Caddy edge — Rishi-owned via
   yral-rishi-hetzner-infra-template repo per A2 carve-out)
        │
        │  reverse_proxy https://rishi-4:443 https://rishi-5:443
        ▼
   rishi-4 + rishi-5 (Caddy as Swarm service, TLS internal)
        │
        ▼
   Swarm overlay yral-v2-public-web
        │
        ▼
   yral-rishi-agent-public-api (3 replicas, load-balanced by Swarm)
        │
        │  (overlay yral-v2-internal)
        ▼
   yral-rishi-agent-conversation-turn-orchestrator + others
        │
        │  (overlay yral-v2-data-plane)
        ▼
   Patroni Postgres + Redis Sentinel + Langfuse (rishi-4/5/6)
```

NO Cloudflare Tunnel anywhere (refined away 2026-04-24 evening).
NO laptop-as-backend after Day 8 (Days 1-3 only, for template dev).

Source: CONSTRAINTS A2 (Caddy carve-out), A15, C3, C5, C10.

---

## 📊 Phase order (canonical, reconciled 2026-04-27)

| Phase | What ships | Priority # |
|---|---|---|
| 0 | Foundations: Sentry baseline cron, template, cluster, hello-world live on cluster, Motorola hits real cluster | — |
| 1 | MVP turn (feature parity): public-api + orchestrator + influencer-directory + Day 9 ETL | — |
| 2 | Memory + Depth | 1 |
| 3 | Quality (soul-file-library + content-safety-and-moderation, bundled) | 2 |
| 4 | Billing integration (payments-and-creator-earnings) | 3 |
| 5 | Proactivity + first-turn nudge | 4 + 7 |
| 6 | Programmatic AI influencer creation (skill-runtime + extended influencer-directory) | 5 |
| 7 | Creator tools + analytics (creator-studio + events-and-analytics) | 8 |
| 8 | Creator monetization + private content (media-generation-and-vault) | 9 |
| 9 | Meta-AI advisor | 10 |

NO Phase 10 (cluster deploy is folded into Phase 0). NO Phase 11 (cutover is at Rishi's discretion per A6, no timeline).

Source: priority doc + TIMELINE.md + this file. Earlier drafts had inconsistent numbering (Codex flagged 2026-04-27); now reconciled.

---

## 📝 Source-of-truth chain

When two docs disagree:

```
   CONSTRAINTS.md  ◀── always wins
        │
   CURRENT-TRUTH.md (this file)  ◀── second
        │
   priority doc (refined-priority-order-locked-2026-04-23.md)
        │
   TIMELINE.md
        │
   README.md (plan-and-discussions/)
        │
   README.md (root of monorepo)
```

If anything below CONSTRAINTS contradicts CONSTRAINTS, the lower doc is wrong and gets updated. Coordinator owns the reconciliation.

---

## 🚧 Codex audit 2026-04-27 — what was fixed

Codex (Rishi's parallel session) identified 5 areas of doc drift. Fixes applied 2026-04-27:

1. ✅ **TIMELINE guardrail line 23** — was "Cloudflare Tunnel" (stale). Updated to "real cluster from Day 8+".
2. ✅ **TIMELINE structural drift** — Day 2 + Day 3 hello-world checklist were appearing AFTER Phase 0 boundary. Folded back into Phase 0 narrative.
3. ✅ **TIMELINE Days 7-8 / Days 9-10 confusion** — pre-Phase-0-expansion labels. Renumbered with explanatory note.
4. ✅ **TIMELINE laptop-IP test block** — stale (laptop-ip + flag setup). Replaced with real-cluster testing language per A15.
5. ✅ **README 402 references** (lines 972, 991, 1222, 1241) — paywall is NOT 402. Updated all to ApiResponse envelope. Added cross-reference to Section 11.8.2.
6. ✅ **Root README phase ordering** — was inconsistent with priority doc. Reconciled to canonical 9-phase order.
7. ✅ **Root README "Build authorized" / "awaiting build" ambiguity** — replaced with single clear status block.
8. ⚠️ **Doc standard heaviness** (Codex Finding 5) — Codex recommended scaling back from line-by-line role comments + 8 docs to file-header + function WHAT/WHY + 5 docs. **NOT changed unilaterally.** Surfaced to Rishi for explicit decision (see "Open question for Rishi" below).

---

## 🧱 Doc role boundaries (added 2026-04-27 per Codex 2nd-pass top-recommendation)

To prevent future drift, each doc has ONE primary purpose. When in doubt about where to write something:

| Doc | Primary purpose | What does NOT go here |
|---|---|---|
| **CONSTRAINTS.md** | Locked truth — every binding rule | narrative, sequencing, prose |
| **CURRENT-TRUTH.md** (this file) | Single-source-of-agreement when other docs disagree | full constraint detail (link to CONSTRAINTS) |
| **TIMELINE.md** | Executable day-by-day sequence | architectural decisions (link to CONSTRAINTS), narrative |
| **README.md (plan)** | High-level narrative, vision, capability blueprints | day-by-day sequence (link to TIMELINE), full constraint list (link to CONSTRAINTS) |
| **V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md** | Infra + template reference | product roadmap, constraints |
| **multi-session-parallel-build-coordination/** | How sessions run + state persistence + Codex review | service architecture |
| **Specialized subfolders** (priority order, mobile audit, LLM routing, etc.) | Topic-deep dives referenced from above | locked rules (link to CONSTRAINTS) |

**Coordinator's job at every doc edit:** ask "is this content in the RIGHT doc per the table above?" If not, move it. This prevents the kind of drift Codex caught.

---

## ❓ Open question for Rishi (post-Codex 2nd-pass audit)

**Codex Finding 5 (refined in 2nd pass):** the doc standard (B7 + F8) requires line-by-line role comments + 8 required docs per service + CI-enforced >50% comment density. Codex's specific recommendation:

```
   Required EVERYWHERE:
     • File header (per B7)
     • Function WHAT / WHY (per B7)
     • DEEP-DIVE.md
     • RUNBOOK.md
     • GLOSSARY.md
   
   OPTIONAL for complex services only (orchestrator, public-api, etc.):
     • WALKTHROUGH.md
     • WHEN-YOU-GET-LOST.md
   
   REMOVED:
     • Line-by-line mandatory role comments
     • CI-enforced comment-density (>50%)
```

**My counter (Coordinator):** Rishi explicitly chose the heavy standard for ADHD + non-programmer reading. Codex doesn't know that context. Mitigations are in place (ROLE-not-SYNTAX comments rot less; CI lint enforces; AI agents re-comment per PR). Trade-off: heavier maintenance vs. easier comprehension.

**Rishi DECIDED 2026-04-27: option (a) — keep B7 + F8 as-is.**

Reason: Rishi is non-programmer + has ADHD; comprehension matters more than maintenance speed. The mitigations (ROLE-not-SYNTAX comments, CI lint, AI re-commenting per PR) make the heavy standard sustainable. Codex's recommendation (option b) was reasoned from generic industry standard, not Rishi's specific situation.

Locked standard:
- Line-by-line role comments on every non-trivial line
- 8 required docs per service (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY + WALKTHROUGH / GLOSSARY / WHEN-YOU-GET-LOST)
- CI-enforced >50% comment density
- Functions in priority order, not alphabetical
- RELATED FILES footer at bottom of every file

This is now closed. CONSTRAINTS B7 + F8 stand as written.

---

## 📝 Codex 2nd-pass — what was further fixed (2026-04-27)

After Codex's 2nd pass:

- ✅ TIMELINE pre-Day-1 questions section (lines 425-440) was stale (referenced laptop-IP, Cloudflare tunnel, Firebase override). Updated to current real checklist.
- ✅ TIMELINE phase-numbering at lines 425-427 was stale (Phase 4 = "memory + streaming + proactivity"). Updated to canonical phase order (Phase 4 = Billing).
- ✅ Mobile contract doc (`mobile-client-api-contract-and-single-change-requirement/how-mobile-client-consumes-v2-through-one-public-api.md`) bundled SSE streaming into v2.0 mobile release — contradicting A16. Rewrote to feature-parity-first + Phase 3+ mobile changes.
- ✅ Doc role boundaries codified in this file (Codex's good architectural insight; prevents future drift).

Push-back on Codex (NOT applied):
- 🛑 "Rewrite TIMELINE.md from scratch" — surgical fixes are sufficient; full rewrite is overkill and risks breaking cross-references.
- 🛑 "README.md = high-level narrative only" — too big a rewrite for marginal value. README has accumulated valuable detail; people search it for specific content. Boundaries codified above without forcing content migration.

Most of Codex's 2nd-pass citations referenced fixes that ALREADY landed in 1st-pass response (paywall 402, root README phase ordering, build-mode ambiguity). Likely cached read from before fixes were committed. Verified all earlier fixes are still in place.

---

*Last updated: 2026-04-27 by coordinator after Codex 2nd-pass audit. Update timestamp every time a binding decision changes.*
