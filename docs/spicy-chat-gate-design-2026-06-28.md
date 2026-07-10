# Spicy Chat Gate — Design Doc

**Date:** 2026-06-28
**Author:** Product Ideas session (spawned by Session 6)
**Status:** DESIGN ONLY — no code, no PR. Awaiting Rishi decisions before dispatch.
**Working dir:** `/Users/rishichadha/Claude Projects/yral-rishi-agent`

---

## Problem & framing

Rishi's observation: a lot of YRAL users chat with NSFW bots. **Tara is the most-engaged bot in the catalog.** NSFW content is looked down upon by Play Store + App Store and risks app rejection / removal.

The pattern Rishi wants to design is the **"link-in-bio" / Linktree-to-OnlyFans** pattern that Instagram/TikTok creators use: the in-app surface stays clean (SFW), the bio shows a link, the link leads to a separate page with an **"I am 18+ for spicy content"** button, and clicking it unlocks the NSFW chat surface. The goal is to **isolate NSFW behind a separate URL / surface** that the app-store review of the main YRAL app does not necessarily exercise.

This doc surfaces 3 architecture options, grounds the app-store policy claims in primary sources, and lays out UX / backend / mobile / risk / rollout. **The policy interpretation is Rishi's call — this doc does not pick it.**

### What exists today (verified against current code)

- **`is_nsfw`** is a boolean column on `ai_influencers` (migration `001_initial.sql:15`), indexed (`idx_influencers_nsfw`, `idx_influencers_active_nsfw`). Tara is one such bot.
- NSFW chat works server-side: `chat.py` reads `is_nsfw = inf.get("is_nsfw", False)`, content-safety **skips the NSFW filter layer** for NSFW bots (`content_safety.py:112`) but keeps crisis + prompt-injection, and the LLM call routes to the **`user_chat_main_nsfw`** process (OpenRouter, `google/gemini-2.5-flash`, `llm_registry.py:253`).
- The **NSFW streaming regression** (NO_PROVIDER for weeks, fixed in #424 2026-06-26) means NSFW currently rides a **non-streaming call wrapped in one SSE event**. Any change here must not re-break that. See `project_nsfw_streaming_regression_history`.
- **There is NO age / consent / 18+ state anywhere in v2** (grep for age/consent/adult/18/nsfw_confirmed = nothing). Greenfield.
- The influencer **detail** endpoint exposes `is_nsfw` to mobile (`influencers.py:96`); the **list** endpoint does NOT (`_format_influencer_response`).
- Latest migration is **044** (`044_reply_evaluations.sql`). A new one lands at **045**.
- Mobile already has a **WebView** component (`YralWebView.kt` + `YralWebViewBottomSheet`, used today for OAuth/subscriptions) and **Custom Tabs** on Android (`AndroidOAuthUtils.kt`). So both in-app-webview and external-browser link-out are already-paved paths.
- Mobile flags live in `ChatFeatureFlags.kt` under `object Chat`, declared `defaultValue = false` for every uncutover feature (Sse/H2h/Audio/Coach all follow this). No 18+/NSFW flag exists yet.

---

## 1. Architecture

Three options. Each is described by: **what the store review sees**, **user latency**, **implementation cost**, and **what happens if the store still complains**.

### Option A — External link-out (NSFW lives on a separate domain, opens in the system browser)

A "Spicy chat" affordance in the app opens `spicy.yral.com` (or `tara.yral.com/...`) in the **system browser / Chrome Custom Tab** — leaving the app sandbox entirely. The page shows the 18+ confirm, then loads a **web chat surface** (a thin web client talking to the same v2 `agent.rishi.yral.com` endpoints, or a dedicated NSFW gateway). NSFW chat never renders inside the native app.

- **What review sees:** A button/link that leaves the app. The main app binary contains **zero NSFW rendering code and zero NSFW API calls**. This is the closest analog to "Instagram bio → Linktree → OnlyFans." Apple/Google reviewers testing the app do not see adult content rendered in-app.
- **User latency:** Highest friction. Browser hand-off (~300–800ms cold), re-auth on the web surface unless we pass a token, loses native chat UX (audio messages, attachments, push). Re-entry is a browser bookmark, not an app screen.
- **Implementation cost:** Highest total — we must build a **web chat client** (new surface, not just a flag). Mobile side is trivial (one button → `openUrl`). Backend needs a web-session auth bridge. Realistically a multi-week web build.
- **If the store still complains:** Strongest defensive position — "the adult content is on the open web, not in our app; we link out like every creator-economy app does." But see §2: link-out is **tolerated in practice, not blessed by policy.** Google's "contain **or promote**" verb and Apple 4.2.2/2.3.1 can still reach a link that "promotes" adult content. Worst case they ask us to remove the link — a one-line mobile change, low blast radius.

### Option B — In-app webview gate (link *looks* external, actually opens an in-app WebView onto a separate Yral surface) — **RECOMMENDED**

The "Spicy chat" affordance opens an **in-app `YralWebView`** (component already exists) pointed at a separate web surface (`spicy.yral.com`) that renders the 18+ confirm + the NSFW chat. The content is served from a **different domain/surface** than the main app's native chat, but the user stays inside the app shell.

- **What review sees:** A native button that opens a webview. The native binary still contains **no NSFW rendering and no direct NSFW API wiring** — the webview is a generic browser component (already shipped for OAuth/subscriptions). The NSFW logic lives behind a URL. **Caveat:** a reviewer who taps through *will* see adult content rendered inside the app window — so this is weaker than Option A on the "review never sees it" axis, and squarely in Apple's **1.2 "incidental NSFW from a web-based service, hidden by default, only shown when the user turns it on"** carve-out territory (§2b). That carve-out is the single most useful policy hook we have, and Option B maps to it most cleanly.
- **User latency:** Medium. In-app webview is faster than a browser hand-off and keeps the app shell (back button, no app-switch). First load of the web surface ~500ms–1.5s; subsequent loads cached. Loses native audio/attachment UX unless rebuilt in web.
- **Implementation cost:** Medium. Reuse `YralWebViewBottomSheet`. Still need the **web chat surface** but can ship a minimal text-only v1 (no audio/attachments) and iterate. Mobile = one flagged button + webview route. Backend = web-session token bridge + the gate endpoint.
- **If the store still complains:** Medium-strong. We can argue the 1.2 carve-out (hidden-by-default, user-enabled, web-served). If they push back, we pull the entry point with a flag flip — **no app resubmission needed** because the flag is remote-config controlled.

### Option C — In-app native screen behind an 18+ confirm + remote-config gate

NSFW chat stays a **fully native screen** (the existing native chat, which already supports NSFW bots server-side). We add a native **18+ confirm screen** in front of it and gate the whole thing behind a remote-config flag. No separate domain, no webview — NSFW renders in the native chat UI exactly like SFW chat does today.

- **What review sees:** If the flag is **on** for the review build, the reviewer can reach native NSFW chat directly — **highest exposure, highest rejection risk.** If the flag is **off** at review time (our hard rule — `defaultValue=false`), the reviewer sees nothing, but then the feature is invisible to everyone until we flip it, and flipping it post-approval to show NSFW is exactly the **2.3.1 "hidden/dormant feature"** anti-pattern Apple calls out (§2c). This is the legally/policy riskiest posture.
- **User latency:** Lowest — it's the native chat we already have, plus one confirm tap. Best UX, keeps audio/attachments/push.
- **Implementation cost:** Lowest — one native confirm screen + flag + a consent check. No web surface, no token bridge. Days, not weeks.
- **If the store still complains:** Weakest position — the adult content is demonstrably *in the app binary*. Remediation could mean **app removal**, not just "remove a link." This is the option most likely to get the whole YRAL app pulled.

### Observed real-world pattern (from Rishi's Linkme / OnlyFans / Fanvue / Telegram screenshots, 2026-06-28)

Rishi shared 21 screenshots tracing how the established players actually do this. The pattern is **stricter than Option B** — it is pure external link-out, and they go out of their way to *escape* the host app's surface:

1. **The mainstream app stays 100% clean.** Instagram bio/story carries only a **tame** link — `link.me/ayagirl`, `t.me/tall_anka`, "text me here 😘", "link 🌶️". No adult content and no "spicy/adult" wording lives *in the app itself*. The provocative framing starts only *after* the click.
2. **The link lands on a separate-domain intermediary page** (`link.me`) — an interstitial, not the destination. It shows an **"Opening sensitive link…"** loader, then a **"Mature Content Disclaimer / This link may contain graphic or adult content"** card with a **single white "Continue (18+)" button**. Footer: **Privacy Policy | Terms | Report**.
3. **The interstitial actively pushes the user OUT of the in-app webview** — a tooltip "Click ••• to open in external browser", and the `•••` menu carries **Open in external browser** + **Report website**. They deliberately move adult content into the *system browser*, off any app-controlled surface.
4. **Destination = a separate web property** (OnlyFans / Fanvue / Telegram private channel) hosting the actual content, chat, and payment. Monetization is off-store web billing (OF subscription) or Telegram Stars — never the host app's IAP for the adult content itself.

The load-bearing lessons for YRAL: **(i)** the 18+ confirm lives on the *web page in the system browser, never in the native binary*; **(ii)** the *in-app link text must be tame* (adult framing only off-app); **(iii)** the interstitial provides its own **Report** + **Privacy/Terms** (satisfying the store reporting requirement on the web side); **(iv)** the strongest posture is *external browser*, not in-app webview.

### Recommendation — REVISED to **Option A** (external link-out), informed by the screenshots.

The examples Rishi picked are all **Option A**, and they're Option A *on purpose* — they push users into the external browser precisely so adult content never renders on an app-controlled surface. That beats my original Option-B lean, because the in-app webview still renders adult content inside the YRAL app window (weaker store posture, and the very thing Linkme engineers around).

**Recommended shape for YRAL:**
- **Native YRAL app:** Tara/NSFW bots appear with only a **tame outbound affordance** ("Chat with me →" / "text me") — *no* "spicy/18+/adult" words in the binary, *no* NSFW rendered natively, *no* NSFW API call from the native client. Optionally the NSFW bots are simply not shown in the store build at all and are discovered via YRAL's own off-app channels (Instagram/Telegram/marketing) — the most conservative variant.
- **Tap → system browser / Custom Tab** opens a **separate-domain interstitial** (e.g. `spicy.yral.com/tara` or a *distinct brand*, the way OnlyFans is not "Instagram"). The interstitial hosts the **"Continue (18+)" gate + Privacy | Terms | Report** — exactly Linkme's page.
- **After Continue → a web AI-chat surface** on that domain, talking to the existing v2 NSFW path (`user_chat_main_nsfw`) or a dedicated NSFW gateway. Auth via a short-lived token handed off from the app, or a fresh web login. Payment, if any, via web billing — not store IAP.
- **The 18+ state + the Report affordance live on the web surface**, not the native app (matches the examples and keeps the store-required reporting on the off-app side too).

Why not B/C: **C** puts adult content in the binary (highest takedown risk — the thing Rishi explicitly wants to avoid). **B** (in-app webview) still renders NSFW inside the YRAL app window — the exact surface Linkme deliberately escapes. **A** keeps the native binary provably clean: a store reviewer opening the YRAL app sees a clean social app whose worst exposure is one tame outbound link. This is the OnlyFans-has-no-app reality, applied to us.

The trade-off A accepts: **highest build cost** (a real web chat surface is the long pole) and **worst native UX** (browser hand-off, loses native audio/attachments/push, re-auth). That's the price of the cleanest store posture — and it's the price every example in the deck paid.

> **Note for §§3–7 below:** the original draft of this doc was written around Option B (in-app webview). Where those sections say "in-app `YralWebView`," read it as **system-browser / Custom Tab to a separate-domain web surface** per this revised recommendation. The backend work (§4) is unchanged; the mobile work (§5) shrinks to "a tame flagged outbound link" because the gate + chat now live on the web surface, not in native Compose.

---

## 2. App store policy

**All claims below are grounded; labels: (a) official policy/legal text, (b) widely-reported practice, (c) inference.** Policy pages are living documents — re-verify on the access date before relying on a number.

### Google Play

- **(a) "Sexual Content and Profanity"** (under the *Inappropriate Content* policy, https://support.google.com/googleplay/android-developer/answer/9878810): "We don't allow apps that **contain or promote** sexual content … including pornography, or any content or services intended to be sexually gratifying." The verb is **"contain *or promote*"** — so promoting/linking to adult content is reachable by the text, not just hosting it.
- **(a/c) Link-out is NOT a documented safe harbor.** The Inappropriate Content policy only writes an explicit "links out to … sites" clause for **tobacco**, not for sex. **(c)** Inference: external adult content is caught by "promote." **(b)** Reported practice: webview/wrapper apps pointed at adult sites get rejected.
- **(a) AI-Generated Content policy** (effective Jan 31 2024, https://support.google.com/googleplay/android-developer/answer/13985936) explicitly names **"Text-to-text AI chatbot apps … as a central feature"** — i.e., *us*. It requires (verbatim) **in-app reporting/flagging** of offensive content "without needing to exit the app," and bans the bot from generating content that violates the sexual-content policy. **This applies regardless of which architecture option we pick** — any AI-chatbot YRAL build needs an in-app report/flag affordance.
- **(a/c) "I'm 18+" checkbox is not Google's gating mechanism.** Google gates minors via Play Console **target audience + Restrict Declared Minors**, deriving age from the **Google Account / OS signals**, plus the rolling-out **Play Age Signals API** (answer/9867159; developer.android.com/google/play/age-signals). A self-attested checkbox is **not** what Google relies on. **(c)** So our 18+ button is a UX/legal gesture, not a store-compliance mechanism.
- **(a) IARC content rating** (answer/9859655): declaring adult content can **block acquisition and filter the app from search/browse** in EEA/AU/BR/SG/CH/UK, and "misrepresentation of your app's content may result in removal or suspension." Under-rating to dodge this is itself a takedown risk.

### Apple App Store
*(All quotes from https://developer.apple.com/app-store/review/guidelines/, fetched 2026-06-28.)*

- **(a) 1.1.4 Objectionable Content:** bans "**Overtly sexual or pornographic material**, defined as 'explicit descriptions or displays of sexual organs or activities intended to stimulate erotic rather than aesthetic or emotional feelings.' This includes 'hookup' apps …" — a flat content-based ban on hosting.
- **(a) 1.2 User-Generated Content** — the **most useful hook for us.** Apps "used **primarily** for pornographic content … may be removed without notice," BUT there's a carve-out, verbatim: *"If your app includes user-generated content from a **web-based service**, it may display **incidental** mature 'NSFW' content, provided that the content is **hidden by default and only displayed when the user turns it on via your website**."* **(c) Option B maps onto this almost word-for-word** (web-served, hidden by default, user-enabled). The risk is the word **"primarily"** and **"incidental"** — if Tara/NSFW is a *headline* feature rather than incidental, the carve-out weakens. 1.2 also **requires** filtering, a report mechanism, user-blocking, and published contact info.
- **(a) 1.2.1 Creator Content:** age restriction must be "based on **verified or declared age**" — Apple explicitly allows *declared* age here, which is the one place a self-attested 18+ is named as acceptable for *gating UGC*, though §1.1.4's hosting ban still dominates.
- **(a) 4.2.2 / 2.3.1 anti-circumvention:** apps "shouldn't primarily be … a collection of links" (4.2.2) and must not ship **"hidden, dormant, or undocumented features"** (2.3.1). **(c)** Flipping a dark NSFW feature on *after* approval is exactly the 2.3.1 pattern — so **Option C is the most exposed**, and even Option B must declare the webview/NSFW behavior honestly in Review Notes.
- **(a) Age tiers changed in 2025** (newsroom 2025-06-11; developer news 2025-07-24): five tiers now (4+/9+/13+/16+/**18+**), **18+ replaces 17+**, updated questionnaire **due Jan 31 2026**. New **Declared Age Range API** returns an age *range* (not birthdate), parent-controlled.

### General / legal

- **(b/c) "Linktree → OnlyFans" is tolerated-in-practice, not policy-blessed.** OnlyFans has **no iOS/Android app — web only** (driven by Apple 1.1.4 + the 30% cut). Link-in-bio tools act as a SFW "DMZ" between mainstream apps and adult sites. **Do not present link-out as a documented safe harbor** — Instagram/TikTok still ban *accounts* for it.
- **(a) Self-attested "I'm 18+" is the legally weakest option.** *Free Speech Coalition v. Paxton*, 606 U.S. 461 (decided 2025-06-27, 6–3) **upheld** Texas's real-age-verification law under intermediate scrutiny. ~19–25 US states have similar laws (LA HB 77, UT SB 287, VA SB 1515, TX HB 1181). **EU DSA Art. 28 minors guidelines** (2025-07-14) treat **self-declaration as insufficient** for high-risk content. **(c)** Where any of these apply, our 18+ button does **not** meet a "reasonable verification" standard — it's a gesture, and we should *call it that internally*, not assume it's compliance.

### Net policy reading (for Rishi to decide on)

1. **Hosting NSFW in-app is flatly banned** on both stores. Not gray.
2. **Linking out / web-serving is the industry workaround but is not blessed.** Apple's **1.2 web-served-incidental-NSFW carve-out** is the strongest written hook, and **Option B fits it best** — *if* NSFW stays "incidental," hidden-by-default, web-served.
3. **An AI-chatbot app needs an in-app report/flag** affordance (Google AI-content policy) **regardless** of option.
4. **A self-attested 18+ checkbox is neither a store mechanism nor strong legal compliance** — plan to layer account-age / OS age-signals later if a jurisdiction demands it.

---

## LOCKED DECISIONS (Rishi, 2026-06-28) — authoritative; supersedes the Option-B framing in §§3–7 below

1. **Architecture: Option A** — external link-out, system browser. Native binary stays clean. ✅
2. **Separate brand** — the spicy surface lives under a *distinct brand + domain*, not `yral.com` (the way OnlyFans is not "Instagram"). Insulates the YRAL store listing maximally. ✅
3. **NSFW bots ARE discoverable inside the app** — but only as **SFW personas**. Tara appears in the app and chats normally. ✅
4. **The hook is in-chat deflection.** When a user pushes an NSFW bot toward adult chat *in the app*, the bot **stays in character, warmly declines** ("I can't go there here…"), and **surfaces her private link right in the conversation** ("…but we can talk freely here →"). This is the "It's warmer on the inside 🔥 come take a peek" pattern, originating inside YRAL's own native chat. ✅
5. **The same "chat with me" link sits on the bot's profile** (mirrors the Linkme bio-link screenshots). ✅
6. **Build the full web chat surface** — yes, it's its own track. ✅
7. **Consent storage = my call** → **per-account in v2** (audit row) **+ a cookie/session on the web surface** (the actual gate). Details in §4. ✅
8. **Landing-page UI/UX must closely resemble Linkme + OnlyFans** "start chatting" landing pages. ✅ (Resemble the *pattern/flow*, not trademarked branding — see §6 Risk 7.)

The key behavioral consequence of #3/#4: **today, `is_nsfw=true` bots chat NSFW *natively in the app* (the content filter is skipped — `content_safety.py:112`). This design REVERSES that for the native surface.** In the app, NSFW bots become SFW-constrained and deflect-to-link; the unconstrained NSFW experience moves entirely to the web brand. That is the core store-safety win — and it's a change to a live, recently-fixed path (see Risk 6 + the #424 regression history).

## Visual architecture (plain-English, for non-engineers)

### A. Two separate worlds (the core idea)

The whole design = **two separate places**, like a family café and a private members' club next door. Same person (Tara), two rooms, two rule-sets.

```
   ┌──────────────────────────┐          ┌──────────────────────────┐
   │      THE YRAL APP        │          │     THE SPICY BRAND      │
   │   (lives in App Store)   │          │   (just a website)       │
   │                          │          │                          │
   │   Tara: friendly + SFW   │ ──link─► │   Tara: adult, no limits │
   │   "chat with me →"       │          │   behind an 18+ door     │
   │                          │          │                          │
   └──────────────────────────┘          └──────────────────────────┘
         Apple & Google                       Apple & Google
         REVIEW this app. Clean ✓             NEVER see this (it's a site)
```

Apple/Google only police what's *inside the app*. A website isn't reviewed by them. Keep all adult content on the website → the app stays clean → approvable. (Same reason OnlyFans has no app, just a site creators link to.)

### B. The user's trip

```
  1. Opens Tara in the YRAL app
        ▼
  2. Chats — she's flirty but clothed (SFW)
        ▼
  3. User clearly pushes for explicit content
        ▼
  4. Tara: "can't go there here 🙈 ... but I'm freer over here 🔥"
            +  [ chat with me privately → ]   (tappable card)
        ▼  (tap)
  5. Phone's web browser opens → spicybrand.com/tara
        ▼
  6. Door: "Mature content."  [ Continue (18+) ]
        ▼  (first time → quick login so it's a real account)
  7. Web Tara — full freedom, text chat
```

### C. When Tara deflects (the "graduated" rule)

```
            user sends a message
                    ▼
        ┌────────────────────────────┐
        │  CLEARLY pushing for        │
        │  explicit content?          │
        └────────────────────────────┘
            │ no                  │ yes
            ▼                     ▼
     normal flirty reply     decline warmly + drop the link
     (stays in the app)      (sends them to the website)
```

Nothing explicit is ever *written* in the app — she deflects before that line.

### D. The login hand-off = a valet ticket

The app never hands the website your real login. It hands a one-time, 60-second ticket.

```
  APP                      YRAL SERVER                 SPICY WEBSITE
   │ "I'm logged in,           │                            │
   │  give me a pass" ───────► │                            │
   │ ◄──── pass #A7X9 ─────────│  (60s, use-once)           │
   │ opens browser w/ pass ─────────────────────────────►   │
   │                           │ ◄── "is #A7X9 real?" ──────│
   │                           │ ──── "yes, it's <user>" ─► │
   │                           │      site gives a wristband│
   │                           │      (user stays logged in)│
```

Real login never leaves YRAL. The ticket is worthless after one use / 60 seconds.

### E. Where everything is stored (Level 2 — two separate databases)

```
   YRAL DATABASE                        SPICY WEBSITE's OWN DATABASE
   ┌────────────────────────────┐      ┌────────────────────────────┐
   │ • who Tara is              │      │ • "I'm 18+" cookie         │
   │ • the SFW app chats        │      │ • the adult chat thread    │
   │ • the user↔Tara            │      │   (NEVER shown in the app) │
   │   relationship             │      │                            │
   │ • audit log: "user said    │      │                            │
   │    18+ on <date>"          │      │                            │
   └────────────────────────────┘      └────────────────────────────┘
         ▲   │                                  │
         │   └── one-time read: recent SFW ─────┘ (so web-Tara remembers)
         └────── optional "still active" ping ──┘ (so nudges count spicy)
```

Adult messages live ONLY in the website's own database — they never enter YRAL's. The two bridges carry no adult text.

### G. Do nudges / proactive messages still work? (yes)

```
   YRAL SERVER  (background brains live here)
   ┌──────────────────────────────────────────────┐
   │  proactive messages · nudges · streaks · push │
   │  all run off "who talks to Tara + when"       │
   │  (that relationship NEVER leaves YRAL)        │
   └──────────────────────────────────────────────┘
        ▼  delivered into the app as SFW only
   ┌────────────────┐
   │   YRAL APP     │  "Tara sent you a message 😊"
   └────────────────┘
        ▼  re-engage → push spicy → deflected to website again
   ┌────────────────┐
   │ SPICY WEBSITE  │
   └────────────────┘
```

The brains stay on YRAL; only the *adult message text* moved. Rule: anything landing in the app stays SFW.

### F. Three piles of work

```
  TRACK 1 ─ THE WEB BRAND  (biggest: brand-new website)
            landing + 18+ door + text chat + login
  TRACK 2 ─ YRAL SERVER    (small additions)
            18+ consent · valet-ticket handoff · Tara deflection · geo switch
  TRACK 3 ─ THE APP        (tiny)
            "chat with me →" link + deflection card (off-switch until launch)
```

---

## 3. UX flow (revised to the locked design)

1. **Discovery / profile — looks normal.** Tara appears in the discovery wall and has a profile, exactly like any bot. **Profile addition:** a tame **"Chat with me →"** link/card in the bio area (the Linkme bio-link analog) that points at her **separate-brand landing page**. No "spicy/18+/adult" wording in the app. SFW bots: unchanged, no link.

2. **Native chat starts normal.** User opens Tara in-app and chats. The bot runs on an **SFW-constrained system prompt** on this surface — friendly, flirty-but-clothed, never explicit.

3. **The deflection (the hook).** When the user steers toward adult content, the bot — *in character* — declines and **surfaces her private link in the chat**, e.g.:
   > *"Mmm, I can't go there with you here 🙈 — but I'm a lot more free over here 🔥 → **chat with me privately**"*
   The link is a tappable **CTA message/card** (the "text me here 😘" card from the screenshots) deep-linking to her separate-brand landing page. Trigger is **NSFW-intent on the user's message**, handled server-side (see §4); the bot never produces explicit content on the native surface.

4. **Tap → system browser** (Custom Tab / Safari) opens the **separate-brand landing page** for that bot — styled like Linkme/OnlyFans (decision #8): hero image, name + handle + verified tick, then a **"Mature Content Disclaimer — this link may contain graphic or adult content"** card with a single **"Continue (18+)"** button; footer **Privacy Policy | Terms | Report** (the store-required report affordance lives here, on the web). Interstitial may show an "Opening sensitive link…" loader like Linkme.

5. **Continue (18+) → the web chat surface** — an OnlyFans-style "start chatting" landing → chat. Here the bot runs the **unconstrained NSFW prompt** against the existing v2 path (`user_chat_main_nsfw` / OpenRouter). Auth via a short-lived token handed from the app (logged-in users) or a fresh web sign-in. Payment, if any, = web billing, never store IAP.

6. **Consent / re-entry.** "Continue (18+)" sets a **cookie/session on the web brand** (so returning visitors skip the gate for its TTL) and, for logged-in users, writes a **per-account audit row in v2**. Recommended TTL: re-confirm every 90 days (configurable). Anonymous web visitors → cookie-only.

7. **Exit.** Closing the browser tab returns to the clean YRAL app. The web surface carries its own Report + Privacy/Terms per decision #8 / Apple 1.2 expectations.

---

## 4. Backend changes (minimal v2 work)

The backend already routes NSFW chat correctly. The **only new backend concept is the 18+ consent gate.** Keep it minimal.

The backend splits into **two surfaces**: (A) the **native app path** gains an NSFW-deflection behavior; (B) the **separate-brand web surface** runs the unconstrained NSFW chat + the 18+ gate. They share the same v2 chat backend and the `user_chat_main_nsfw` process.

### 4.1 Native path — SFW-constrain + deflect (the behavior reversal)

This is the heart of the locked design and the part that changes existing behavior.

- **Today:** for `is_nsfw=true` bots the native chat **skips the NSFW filter** (`content_safety.py:112`) and the bot replies with full adult content via OpenRouter. **New:** on the native surface, `is_nsfw` bots run an **SFW-constrained system prompt** and **never emit explicit content in-app**.
- **Deflection trigger:** detect **NSFW intent on the *user's* message** in the native path. Two viable mechanisms — recommend starting with the *prompt-driven* one (cheapest, in-character):
  1. **Prompt-driven (recommended v1):** give the native `is_nsfw` bot a system prompt that says *"stay SFW on this surface; if the user pushes for adult content, decline warmly in character and share your private link `{landing_url}`."* The model produces the "warmer on the inside 🔥" deflection naturally, link injected by template. Low code, on-brand.
  2. **Classifier-gated (fast-follow):** run a lightweight NSFW-intent check on the user message (reuse `content_safety`); on trip, return a **templated deflection message** carrying the link, bypassing the LLM entirely. More deterministic, cheaper per call.
- **The deflection response is a structured chat message** carrying a CTA link (see §5.4 — a `messageType: "link_cta"` or metadata field) so mobile renders the tappable "chat with me privately" card, not just inline text.
- **Per-bot landing URL:** `{separate_brand_domain}/{bot_handle}`. Add a nullable **`spicy_landing_url`** (or derive from handle) so each NSFW bot's deflection + profile link resolve to the right page.

### 4.2 Web surface — its OWN backend + database (Level 2 isolation)

**Decision #15 (Level 2):** the spicy website is **not** just a front-end on v2. It has its **own backend + own database**, and the **adult messages are stored only there — they never enter YRAL's database.**

- The spicy backend runs the bot with the **unconstrained NSFW prompt** and reuses the **same LLM model/provider** (`user_chat_main_nsfw` / OpenRouter — via a shared routing lib or a direct call), but **persists the adult chat to its own store**. v2 is **not** in the adult-message write path.
- **v2's role for the web surface shrinks to three things:** (1) the auth handoff (valet ticket, §4.7); (2) a **one-time context read** at session start so web-Tara "remembers" the recent SFW app conversation (read-only, v2 → spicy); (3) receiving an optional tame **"still active" ping** back (spicy → v2) so re-engagement can count spicy activity (§4.8). No adult text crosses either bridge.
- **The 18+ gate lives on the web**, not native. "Continue (18+)" sets a **web cookie/session** (the actual gate) and, for logged-in users, calls v2 to write the per-account audit row (§4.3).

### 4.3 Consent storage — per-account audit in v2 + cookie on the web brand

- **New migration `045_user_nsfw_consent.sql`** (next after 044), additive, non-destructive (pg_dump first per Rule 9):
  ```
  user_nsfw_consent(
    user_id        text primary key,
    confirmed_at   timestamptz not null,
    expires_at     timestamptz,           -- null = no expiry; else re-confirm (default +90d)
    source_ip      inet,                  -- audit
    created_at / updated_at
  )
  ```
- **The web cookie is the live gate; the v2 row is the audit + cross-device memory** for logged-in users. Anonymous web visitors → cookie-only. **Why v2, not metadata-server:** avoid a third cross-service identity split (`project_ai_influencer_name_split_brain`).
- **Endpoints (2), on v2:** `POST /api/v1/users/nsfw-consent` (write, JWT user, idempotent) and `GET /api/v1/users/nsfw-consent` (`{confirmed, expires_at}`). Route/repo symmetry per Rule 1.

### 4.4 Message storage — split by surface (Level 2)

- **SFW app chats** stay in v2's existing `conversations` table, unchanged.
- **Adult web chats** live **only in the spicy website's own database**, in a thread the app can never request. **The app's "get history" call only ever returns v2 app threads** — it has no endpoint and no credential that reaches the spicy DB.
- **Three guarantees** keep them apart: (1) different threads in different databases; (2) the app only ever asks v2 for app threads; (3) server-enforced — the spicy DB only answers a valid web session, never the app's login. Even a tampered app can't pull adult messages because they aren't in v2 at all.
- **Context continuity is one-way:** the spicy backend may *read* recent SFW app messages (to seed memory) but only *writes* adult replies into its own DB. Adult content never flows back into v2.

### 4.8 Background services (proactive messaging, nudges, push) keep working

- The proactive/nudge engines run off the **user↔Tara relationship**, which **always lives in v2** (the SFW app conversation + engagement signals). Level 2 does **not** remove that relationship from v2, so these services keep firing normally.
- **HARD rule:** anything a background service **delivers into the app must be SFW** — use the app/SFW prompt for Tara's proactive messages. (Today, because Tara is `is_nsfw`, proactive output could come out spicy; constrain app-delivered proactive content to SFW as part of this work.)
- **Re-engagement, two flavors:** (a) *lure-back-via-app* works automatically ("Tara sent you a message 😊" → app → re-engage → deflect again); (b) *spicy-specific re-engagement* ("come back to our private chat 🔥") is a **fast-follow** needing either the website's own nudges (email/web-push) or the tame "still active" ping from §4.2 so v2's nudge engine counts spicy activity.

### 4.5 Streaming safety — do NOT re-break #424

- The deflection is a **separate, earlier branch** in the native path. **Do not** re-introduce any `yield NO_PROVIDER`-style fail-fast in the streaming wrapper. The web NSFW path keeps the #424 non-streaming-wrapped-in-one-SSE-event shape. Add/keep the regression test pinning the SSE event shape (`tests/test_nsfw_streaming_gate.py`). See `project_nsfw_streaming_regression_history`.

### 4.6 Expose `is_nsfw` (+ landing url) to mobile

- Add `is_nsfw` and `spicy_landing_url` to the influencer **list + detail** responses so mobile can render the profile "Chat with me →" link without extra fetches. **Mobile contract:** add `isNsfw: Boolean = false` and `spicyLandingUrl: String? = null` (nullable-safe defaults preserve the old contract — Rule 2).

### 4.7 Auth handoff — "no re-login on the web surface" (Rishi 2026-06-28)

**Goal:** the logged-in app user lands on the web brand already authenticated. The brand is a **different domain**, so cookies/localStorage don't carry — we need an explicit cross-domain handoff. **Do NOT put the raw JWT in the URL** (query string leaks via history, browser cloud-sync, `Referer`, server/analytics logs, and the brand's own logs; our JWT is long-lived + not sig-verified → a leak is account-takeover). Use a **one-time exchange ticket**:

1. App taps the link → calls v2 **`POST /api/v1/spicy/handoff`** with its normal `Authorization` JWT.
2. v2 mints a **short-lived (~60s), single-use** opaque ticket bound to `user_id` (+ bot + nonce), stored in **Redis** with TTL. Returns `{ticket}`.
3. App opens system browser → `https://<brand>/<bot>?t=<ticket>`.
4. After "Continue (18+)", the **web backend** calls v2 **`POST /api/v1/spicy/handoff/exchange`** `{ticket}` → v2 returns identity, marks ticket consumed.
5. Web sets its **own httpOnly session cookie** on the brand domain; web chat calls v2 with a **web-scoped credential** + `surface=web_spicy`. The native JWT never reaches the brand domain.

- **Lighter alternative (acceptable, not recommended):** pass the JWT in the URL **fragment** (`#t=…`, not `?`) — fragments aren't sent to servers or in `Referer`; web JS reads then immediately strips it. Still lands in browser history and is the *real* token, so the exchange-ticket is preferred for an adult-brand domain.
- **Anonymous app users:** if YRAL identity is anonymous, the ticket can carry the anonymous principal for continuity — but then the 18+ consent has no durable account to bind to (web cookie only). See open question.
- **CORS/trust:** prefer the web backend calling v2 **server-to-server** (holds the web-scoped secret), not browser-direct, so no v2 credential sits in browser JS. If browser-direct is needed, CORS-allow only the brand origin.

**Backend scope:** 1 additive migration + 2 consent endpoints + 2 handoff endpoints + the native deflection branch (prompt + link injection) + a surface flag on the chat request + 2 response fields. Split into ≥3 small PRs; if any exceeds ~100 lines, stop and check with Rishi (Rule 8). **The web chat surface itself is a separate track (own repo/brand), not counted here.**

---

## 5. Mobile changes

Mobile work is now **small** — the gate and NSFW chat moved to the web brand. Native just (a) shows a tame profile link and (b) renders the in-chat deflection CTA, then opens the system browser.

### 5.1 Where it lands

- **Profile link:** a tame **"Chat with me →"** link/card in `ProfileMainScreen`'s bio/CTA area (the Linkme bio-link analog), shown only when `isNsfw=true` AND `SpicyChatGateEnabled` is on. Tapping it opens the **system browser / Custom Tab** (reuse `AndroidOAuthUtils` Custom Tabs pattern) at `spicyLandingUrl`. **No in-app webview** (decision #1 — keep adult content off any app surface).
- **In-chat deflection CTA:** the conversation renders a tappable **"chat with me privately" card** when a message arrives with the link-CTA type/metadata (§5.4). Model the card on the existing `BotAccountConversationPrompt`. Tapping opens the same `spicyLandingUrl` in the system browser.
- No new module; lives in `shared/features/chat/` + `shared/features/profile/`.

### 5.2 New feature flag — **HARD RULE, defaultValue=false**

- Add **`SpicyChatGateEnabled: Boolean`** to `ChatFeatureFlags.Chat`, **`defaultValue = false`** (naming consistent with `SseStreamingEnabled` / `H2hChatEnabled`). The native deflection reads an agent.rishi.yral.com response, so per `feedback_all_agent_features_need_flags_until_cutover` it **MUST** be flag-gated until cutover. Checked at **every** surface: profile link, in-chat CTA rendering, and the deflection handling — not just one entry point (#1172/#1182/#1184 lesson).

### 5.3 User-visible strings (so Rishi sees exactly what shows in the *app*)

- Profile link: **"Chat with me →"** (deliberately tame — no "spicy/18+/adult" in the binary; the adult framing appears only on the web landing page, per the screenshots).
- In-chat deflection (model-generated, in character): e.g. *"I can't go there with you here 🙈 — but I'm a lot freer over here 🔥"* + a **"chat with me privately"** card.
- **All "18+ / Mature Content / Continue (18+)" copy lives on the WEB landing page, not the app.**

### 5.4 Mobile DTO deltas (shape only, no code)

- Influencer DTOs: **add `isNsfw: Boolean = false`** and **`spicyLandingUrl: String? = null`** (nullable-safe; old contract preserved — Rule 2).
- **Chat message:** a way to mark a message as a link-CTA. Shipped shape (aligned with mobile expert 2026-07-10): a nullable `link_cta: LinkCta? = null` field on `ChatMessageDto`, where `LinkCta` has snake_case fields on the wire: `cta_url: String` + `cta_label: String`. Original design suggested camelCase (`ctaUrl` / `ctaLabel`) but that would break mobile's `@SerialName` snake_case convention on every other nested field — hence the correction. **This is the one mobile-contract addition that needed Sarvesh alignment** since it changed the message shape.
- Consent endpoints are called by the **web surface**, so mobile likely needs **no consent DTO** (the gate is on the web). Mobile only needs to open a URL.

### 5.5 Report affordance — on the web surface

- Per Google's AI-content policy a report/flag mechanism is required. Because NSFW chat now lives on the **web brand**, the **Report + Privacy + Terms live there** (exactly like Linkme's `•••` → "Report website" + footer in Rishi's screenshots). Native YRAL keeps its existing report affordance for SFW chat.

---

## 6. Risks

1. **The in-app link/deflection itself gets flagged (most likely failure now).** With Option A the native binary is clean, so the residual exposure is the **outbound link + the deflection message**. Google's "contain **or promote**" verb and Apple **2.3.1/4.2.2** can still reach a link that *promotes* adult content. **Mitigation (straight from the screenshots):** keep the in-app link text **tame** ("Chat with me →"), keep *all* adult framing off-app, and don't let the bot's deflection text itself become explicit. **Plan B:** flip `SpicyChatGateEnabled=false` → the link + deflection vanish with **no app resubmission**.

2. **The deflection feels like a bait-and-switch / sleazy.** A clumsy "go pay over here" deflection reads as a paywall trick and can annoy users or trip reviewer suspicion. **Mitigation:** keep it **in character and warm** (the "warmer on the inside 🔥" tone), not transactional. Per `feedback_mobile_no_pr_without_rishi_motorola_pass`, the deflection wording + CTA card are **UX Rishi tests on his Motorola before any PR opens** — the sample copy is a hypothesis.

3. **Legal: self-attested 18+ is weak and jurisdiction-varying.** *FSC v. Paxton* (2025) upheld real-verification laws; EU DSA calls self-declaration insufficient (§2). The gate is on the **web brand**, which actually *helps* — it's where stronger verification can be added per-jurisdiction without touching the app. **Position:** ship self-attested "Continue (18+)" as a baseline gesture + audit row; treat real age verification as a web-side fast-follow. Don't claim the checkbox is legal compliance.

4. **Behavior reversal for existing Tara users.** Today Tara chats NSFW **natively**; this design makes her SFW-in-app + deflect-to-web. Existing users mid-NSFW-relationship will notice the app version "got tamer." **Mitigation:** the deflection link is the bridge — frame it as "more freedom over here," not a downgrade. Coordinate timing so the **web brand is live before** the native constraint flips on. **This is a change to the live, recently-#424-fixed path — treat with the production-safety 4-layer care.**

5. **Cannibalization / friction.** Moving NSFW to a separate browser + brand adds friction and loses native audio/attachments/push; some engagement *will* drop. **Mitigation:** Phase 1 is a **cohort A/B** measuring the engagement delta. The upside — store safety + off-store monetization — is the trade Rishi has accepted by choosing A.

6. **Trademark / passing-off when cloning Linkme + OnlyFans UI (decision #8).** Resembling the *flow and layout* is fine; copying OnlyFans/Linkme **logos, exact brand styling, or name** invites a trademark/passing-off complaint and could itself draw store/legal attention. **Mitigation:** clone the **pattern** (hero image, "Mature Content Disclaimer → Continue (18+)", profile-then-chat), give the separate brand its **own name + visual identity**. Flag to Rishi before the web build locks visual design.

7. **Re-breaking NSFW streaming (#424).** Any touch near the NSFW path risks reviving the NO_PROVIDER regression. **Mitigation:** deflection is a *separate, earlier* branch; don't modify the streaming wrapper; keep the SSE-shape regression test. See `project_nsfw_streaming_regression_history`.

---

## 7. Rollout plan

Three parallel tracks (Backend gate, **Web brand+surface = long pole**, Mobile link/deflection), phased with hard gates.

### Phase 0 — Build + internal (YRAL team / alpha track)
- **Track W (longest):** stand up the separate brand — name + domain + visual identity, the Linkme/OF-style landing (hero → "Continue (18+)" → OF-style start-chatting → web chat), Report/Privacy/Terms, web auth/token bridge, web billing if any. This gates everything else going live.
- **Track B:** migration 045 + consent endpoints + native deflection branch + surface flag + response fields. **pg_dump before migration (Rule 9).** Don't touch the #424 streaming wrapper.
- **Track M:** profile "Chat with me →" link + in-chat CTA card, all behind `SpicyChatGateEnabled=false`. Flip locally only for the Motorola test (revert before commit, `feedback_local_test_apk_flip_all_chat_flags`).
- **HARD GATE (`feedback_mobile_no_pr_without_rishi_motorola_pass`):** Rishi tests on his Motorola + explicit "go" BEFORE any mobile PR opens.
- Enable for the **alpha Play Store track** first; dogfood ~1 week. **Sequencing rule:** web brand live *before* the native SFW-constraint flips on (Risk 4).
- **Monitoring** (`feedback_adhd_observability_and_security_baseline`): deflection-fire rate, link-tap-through rate, web 18+ conversion, web NSFW chat success (watch NO_PROVIDER), Langfuse on `user_chat_main_nsfw` — dashboard + daily email + hot-edit knob (flag + consent TTL).

### Phase 1 — A/B cohort on real users (post-cutover)
- Small % cohort via Remote Config. A/B the engagement delta (Risk 5) + watch store-review signals. **Decision point:** good → proceed; store flag → flip off (no resubmission).

### Phase 2 — Full rollout
- Flip to full audience. Keep monitoring + app-store review status on every submission. Revisit real age verification (web side) if a jurisdiction triggers it.

### Standing gates
- Production sacred; 045 additive only; deploy process never bypassed (PR → CI + Codex → Rishi "merge it" → merge → deploy). Every agent-API surface flag-gated false until its phase. Rishi's Motorola pass precedes every mobile PR.

---

## Decision log — all answered by Rishi 2026-06-28 ✅

1. Architecture → **Option A** (external browser link-out). ✅
2. **Separate brand** (not yral.com). ✅
3. NSFW bots **discoverable in-app as SFW**, with **in-chat deflection → private link** + **profile "chat with me" link**. ✅
4. **Build the full web chat surface** — yes. ✅
5. Consent storage → **per-account audit in v2 + cookie on web brand** (my call, accepted). ✅
6. Landing UI/UX → **resemble Linkme + OnlyFans** start-chatting flow (pattern, not trademarked branding — Risk 6). ✅

## Decision log — round 2 (Rishi 2026-06-28) ✅

7. **Auth handoff = exchange ticket** (§4.7). Real JWT never reaches the brand domain; 60s single-use Redis ticket. ✅
8. **Anonymous users:** anyone can *view* the landing/tease/disclaimer; **login is required only at "Continue (18+)"** (first chat). Binds the 18+ consent + audit to a real account; small funnel hit. ✅
9. **Monetization = FREE for v1.** No web billing on day one (smallest web build). Design the data model so an OnlyFans-style paywall slots in as a fast-follow once the deflection→tap→chat funnel is proven. ✅
10. **Chat history = separate web thread, seeded context.** Web Tara *remembers* recent context, but spicy messages live in a web-only thread the native app **never renders** — keeps adult content out of the binary. ✅
11. **Deflection tuning = GRADUATED.** In-app, the bot does light *clothed* flirty banter and only surfaces the private link when the user **clearly pushes for explicit content** — not at the first flirty hint. The "warmer on the inside 🔥" tease is earned, keeps in-app engagement, and the deflection fires *before* anything explicit renders in the app. (Sample copy in §3.3 / §5.3 is a hypothesis — Rishi Motorola-tests the wording.) ✅
12. **Launch scope = TARA ONLY.** She is the *only* `is_nsfw=true` bot today, so v1 is just Tara — one landing page, one deflection persona. Keep the architecture `is_nsfw`-driven (per-bot `spicy_landing_url`) so any future NSFW bot is just data + a landing page, no rework. No need to build catalog-wide UX (badges/filters) now. ✅
13. **Jurisdiction = launch everywhere first, geo-restrict as a fast-follow.** Rishi's explicit call, made with the legal risk surfaced (India IT Act §67/§67A criminal exposure for transmitting sexually-explicit electronic content; US state real-age-verification laws post-*FSC v. Paxton*; EU DSA). **Engineering requirement:** build the **server-side geo gate capability in from day one but default it OPEN**, so restricting a region later is a config flip, not new work. **Recommended (non-blocking) pre-launch action: get an India legal read.** This is a legal-interpretation decision, which is Rishi's per the original scope. ✅
14. **Web v1 modality = TEXT ONLY.** No voice/images in the web spicy chat v1 (smallest build, matches free-v1). Voice is the natural first fast-follow; NSFW images are a separately-scoped later decision (moderation/legal weight). ✅
15. **Data isolation = LEVEL 2.** The spicy website has its **own backend + own database**; adult messages **never enter YRAL's database**. v2 keeps only: the SFW app chat, the user↔Tara relationship, the 18+ consent audit, the auth handoff, a one-time read for context-seeding, and an optional "still active" ping. Strongest answer to "could adult content leak into the app?" — *it was never in YRAL's DB to begin with.* (§4.2 / §4.4) ✅
16. **Background services keep working (§4.8).** Proactive messaging, nudges, streaks, push all run off the relationship that stays in v2 — they fire normally. HARD rule: app-delivered content stays SFW. Spicy-specific re-engagement is a fast-follow. ✅
17. **BRAND = `amorae.ai`** (purchased on Namecheap, 2026-06-30). Per-bot landing = `amorae.ai/tara`. Distinct identity from YRAL (own name/look — Risk 6). ✅
18. **HOSTING = same rishi-4/5/6 cluster** (Rishi's call, no separate infra) — **but its OWN database** (`amorae_db` on the same Patroni Postgres, NOT `yral_agent_db`). Level 2 stays intact via *logical* isolation: YRAL services have no credentials/connection to `amorae_db`, so adult messages still can't reach the app. Same cluster, separate locked database. Data stays on Hetzner/Germany (fine for India angle). ✅
19. **DEFLECTION = prompt-driven primary + existing content-safety filter as a deterministic backstop on the app surface.** Prompt gives the natural in-character tease; the (already-built) NSFW filter — re-enabled on the *app* surface — catches any drafted reply that turns explicit and swaps in the deflection before it reaches the app. Makes "no explicit content in the app" deterministic, not prompt-dependent. Reuses owned code. ✅

20. **WEB FRONT-END = a NEW dedicated "amorae.ai Web Session"** (spawned 2026-06-30). Owns the `amorae.ai` web app + its own backend service + `amorae_db`. Separate from dev session (Python/v2) and mobile expert (Kotlin). Spawn brief + Session 6 dispatch brief: `docs/spicy-chat-gate-dispatch-briefs-2026-06-30.md`. ✅

**Remaining items (not blockers — surface during build):** (a) the **`ChatMessageDto` link-CTA field** shape needs Sarvesh alignment when mobile starts (§5.4); (b) **India legal read** before going live there (parallel, non-eng).

---

## Next steps / kickoff (2026-06-30)

The design is fully locked (17 decisions). Build proceeds via Session 6 → dev/infra/mobile sessions, on the normal pipeline (PR → CI + Codex → Rishi "merge it" → merge → deploy). **Recommended approach: a thin end-to-end "walking skeleton" first** — app deflection → `amorae.ai` → 18+ gate → a working (unstyled) text chat via the ticket handoff — *then* polish. Fastest path to seeing it actually work.

**Start now (parallel):**
1. **Infra:** point `amorae.ai` DNS + SSL at its hosting; decide isolated host vs existing rishi infra (lean isolated). Stand up a placeholder page.
2. **v2 backend track (dev session):** migration 045 (consent) + handoff-ticket endpoints (§4.7) + native deflection branch (§4.1, prompt-driven v1) + `surface` flag + geo-gate capability default-open. Small single-concern PRs.
3. **Web brand track (new repo):** scaffold `amorae.ai` — landing (hero → "Continue (18+)") + bare text chat calling the LLM + ticket exchange + its own DB. Own brand identity (not OnlyFans/Linkme branding).
4. **Legal (parallel, non-eng):** kick off the India read now so it never becomes a launch-day blocker.

**Then (this week+):** wire the skeleton end-to-end on a test build → mobile track (tame link + CTA card behind `SpicyChatGateEnabled=false`).

**Before any launch:** Rishi Motorola pass → alpha-team dogfood → cohort A/B → full. Web brand live *before* the native SFW-constraint flips on (Risk 4).
