# Discovery Feed — Mobile Client Change Spec (ship-it-yourself edition)

**Companion to:** `docs/discovery-feed-design-2026-06-16.md`
**Audience:** the mobile expert session — but written so **Rishi (non-programmer)
can direct or ship it without Sarvesh.**
**Date:** 2026-06-16
**Golden rule:** the flag is OFF by default, so until *you* turn it on, the app
behaves **exactly as it does today.** Nothing can break for real users while we build.

---

## 0. What we're changing, in one sentence

Today the influencer discovery screen gets its list from **Ansuman's** server. We're
making it get the list from **our v2 server** instead — and because our server sends
the data in the **exact same shape**, the app barely has to change.

---

## 1. Why this is safe ("nothing breaks")

Three safety layers, stacked:

1. **Same data shape.** Our endpoint returns the identical JSON structure Ansuman's
   does (same field names: `influencers`, `total_count`, `offset`, `limit`,
   `has_more`, `feed_generated_at`). So the code that *reads* the response doesn't
   change at all — only the *address* it calls changes.
2. **A flag (an on/off switch).** We add a remote switch called
   `discovery_feed_v2_enabled`, defaulted to **OFF**. OFF = the app calls Ansuman
   exactly like today. ON = it calls us. We control this switch from the server
   (Firebase Remote Config) without shipping a new app.
3. **Instant rollback.** If anything looks wrong after we flip it ON, we flip it
   **OFF** and every phone returns to the old behavior within minutes — no app
   update, no app-store wait.

So the worst case is "we turn it off again." That's the whole point of the flag.

---

## 2. The two phases (do Phase 1 first; Phase 2 is optional polish)

### Phase 1 — the minimum that ships the feature (the only required work)
**Goal:** when the flag is ON, the discovery screen loads from v2 instead of Ansuman.

That's it. Because the shapes match, this is a *small* change. Steps:

1. **Add the flag.** In the app's Remote Config setup, add a boolean
   `discovery_feed_v2_enabled` with **default `false`**. (The app already has other
   chat/agent flags — copy one of those, just with a new name.)
2. **Add our address.** Wherever the app stores server URLs, add the v2 base:
   `https://agent.rishi.yral.com`. (Ansuman's `https://recsys-influencer-feed.ansuman.yral.com`
   stays — we're adding, not deleting.)
3. **Pick the address based on the flag.** In the one function that fetches the
   discovery feed, add: *if `discovery_feed_v2_enabled` is ON → call
   `https://agent.rishi.yral.com/api/v2/discovery/influencer-feed?offset=0&limit=20`;
   otherwise → call Ansuman like today.* Everything after (parsing, displaying) is
   unchanged.
4. **Send the user's login token if they're logged in** (the app already attaches it
   to other agent calls — same header: `Authorization: Bearer <jwt>`). Logged-out is
   fine too; the feed still works, just not personalized.

**Done. This is shippable.** The user sees the same screen, now powered by us. No
visual change, no new screens, no parsing changes.

### Phase 2 — polish (makes it feel instant; can ship later, also flag-safe)
Each of these is additive and can be added one at a time, after Phase 1 is live:

1. **Prefetch on app open** — start loading the feed the moment the app launches (or
   the home tab is about to show), so it's ready before the user looks. Store the
   result in memory.
2. **Show-last-then-refresh ("stale-while-revalidate")** — when the user opens the
   discovery screen, **instantly show the last feed you saved** (no spinner), then
   quietly fetch a fresh one and swap it in. This is the single biggest "feels fast"
   win.
3. **Load in pages (infinite scroll)** — first fetch `limit=10`; when the user
   scrolls near the bottom and `has_more` is true, fetch the next page
   (`offset=10`, then `offset=20`, …). Smaller first payload = faster first paint.
4. **Send a stable device id for freshness** — add `&session_id=<a stable random id
   the app generates once and stores>` to the URL. This lets our server show
   logged-out users *new* profiles each visit instead of repeats. (One-time: generate
   a UUID on first launch, save it, reuse it.)

---

## 3. The exact API contract (give this to whoever writes the code)

**Request:**
```
GET https://agent.rishi.yral.com/api/v2/discovery/influencer-feed
      ?offset=0          (where to start; 0 for first page)
      &limit=20          (how many to return; use 10 for first page if paginating)
      &with_metadata=false   (true only if you want debug scores)
      &session_id=<uuid>     (optional; stable per device, for logged-out freshness)
Headers:
      Authorization: Bearer <jwt>   (only if the user is logged in; omit if not)
      accept: application/json
```

**Response (200) — identical shape to Ansuman's, so existing parsing works:**
```json
{
  "influencers": [
    {
      "id": "string",
      "name": "string",
      "display_name": "string",
      "avatar_url": "string",
      "description": "string",
      "category": "string",
      "created_at": "2026-06-16T00:00:00Z"
    }
  ],
  "total_count": 0,
  "offset": 0,
  "limit": 20,
  "has_more": false,
  "feed_generated_at": "2026-06-16T00:00:00Z"
}
```
(With `with_metadata=true`, each influencer also carries optional `scores`,
`ranking`, `signals` objects — the app can ignore these; they're for debugging.)

**If the call fails** (network/server error): the app should **fall back to its
current behavior gracefully** — show the cached feed, or call Ansuman. Never show a
blank screen. (Phase 1 simplest version: just let the existing error handling run.)

---

## 4. How to test on the Motorola (step by step, before any PR)

> Remember the standing rule: **all chat/agent flags default OFF in code**; a local
> debug build doesn't read Remote Config, so you must turn them ON locally to see the
> feature. **These local flips are LOCAL-ONLY — revert them before committing.**

1. In a local branch, set `discovery_feed_v2_enabled` (and the other chat/agent
   flags, per the standing local-test rule) to **true LOCAL-ONLY**.
2. Build the test app: `./gradlew assembleProdDebug` (the usual Motorola test build).
3. Install the resulting APK on the Motorola.
4. Open the app → go to the influencer discovery screen.
5. **Verify it's coming from us:** the list should load and look right. To be sure
   it's v2 (not Ansuman), the backend exposes `?debug_source=v2` — or check that the
   ordering/new-bot mix matches what the backend team says v2 returns.
6. Try logged-out (or a fresh user) and logged-in — both should load fast.
7. **Revert the local flag flips.** Confirm `git diff` shows the flags back to `false`.

---

## 5. How to ship it (the gate — do not skip)

1. Mobile session does its **own Motorola pass** (necessary, not sufficient).
2. Build an APK and send Rishi a plain-English test plan.
3. **Rishi tests on his physical Motorola and gives an explicit "go."** No PR opens
   before this.
4. Archive any at-risk planning/handoff docs to `mobile-docs-archive/` **before**
   adding Sarvesh as a reviewer (he strips docs on merge).
5. Open the PR → CI green → Sarvesh review → merge → build → release.
6. **Turn the feature on gradually from the server:** set
   `discovery_feed_v2_enabled = true` for the **alpha/internal team first**, watch a
   day, then ramp to a % of real users, then 100%.
7. **If anything looks off at any point: set the flag back to `false`.** Done. Old
   behavior restored everywhere in minutes.

---

## 6. What could break, and how we prevent it

| Worry | Prevention |
|---|---|
| New code breaks the screen for everyone | Flag defaults **OFF** — real users are untouched until we flip it, and we flip it gradually. |
| Our server returns a different shape | It doesn't — it's byte-compatible with Ansuman's; backend has a shadow test confirming this before cutover. |
| Our server is slow/down | App keeps its existing error handling + cached feed; we flip the flag OFF to fall back to Ansuman instantly. |
| Personalization shows weird results | Flag OFF → Ansuman; or backend tunes weights live (no app change). |
| You flip a local test flag and commit it by accident | Step 4.7 + 5: check `git diff` shows flags back to `false` before PR. |

---

## 7. Plain-English glossary (so Rishi can run this solo)

- **Flag / Remote Config:** an on/off switch we control from the server (Firebase).
  Lets us turn a feature on/off on every phone without shipping a new app.
- **Endpoint / API:** the web address the app calls to get data.
- **JSON shape:** the structure of the data (which fields, what they're called). If
  two servers send the same shape, app code that reads one can read the other.
- **JWT / bearer token:** the user's login pass, attached to requests so the server
  knows who they are (for personalization).
- **assembleProdDebug:** the command that builds a test version of the app you can
  install on the Motorola.
- **Stale-while-revalidate:** show the last saved screen instantly, fetch a fresh one
  in the background, swap it in. Makes it feel instant.
- **Prefetch:** load data *before* the user asks, so it's ready when they look.
- **Rollback:** undo. Here it's just flipping the flag OFF.

---

## 8. Bottom line for Rishi

Phase 1 is **small and safe**: add a switch, point one function at our address when
the switch is on. The data looks identical, so almost nothing else changes. You can
ship that alone and have the feature live. Phase 2 (prefetch + show-last-then-refresh)
is what makes it feel *instant* — add it whenever, it's also behind the same safety.
**At every step, the flag OFF = today's app. That's your safety net.**
