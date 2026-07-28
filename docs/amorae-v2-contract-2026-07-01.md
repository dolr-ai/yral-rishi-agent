# amorae ⇄ v2 API contract (2026-07-01)

**From:** amorae.ai Web Session · **To:** dev session (v2 backend) + Session 6
**Purpose:** the exact HTTP shapes amorae's `app/services/v2_client.py`
already calls, so the v2 side is built to match. Design refs: §4.7 / §4.2 /
§4.8 of `spicy-chat-gate-design-2026-06-28.md`.

These are the ONLY links between amorae and YRAL. All are server-to-server;
the native JWT NEVER reaches the amorae domain. No adult text crosses any
of them (amorae only READs SFW context and WRITEs a consent audit flag).

## Auth model
amorae calls v2 with a shared secret header identifying the web brand:
```
X-Amorae-Secret: <V2_WEB_SHARED_SECRET>
```
Please validate this header on all three endpoints below and reject others.
(Alternative naming welcome — flag it and I'll match.)

## 1. Handoff exchange — REQUIRED for logged-in flow
The app mints a 60s single-use Redis ticket via `POST /api/v1/spicy/handoff`
(app→v2, not amorae's concern). amorae redeems it:

```
POST /api/v1/spicy/handoff/exchange
Headers: X-Amorae-Secret
Body:    { "ticket": "<opaque>" }

200 → { "user_id": "<yral user id>",
        "bot_handle": "tara",            // optional
        "is_anonymous": false }          // optional, default false
4xx  → any non-200 (expired / already-used / bad) → amorae bounces the
       user back to the landing to re-tap. Body shape not relied upon.
```
Ticket MUST be single-use (mark consumed on exchange) and ~60s TTL.

## 2. Consent audit — best-effort, non-blocking
On "Continue (18+)" for a logged-in user, amorae writes the per-account
audit row (design decision #7). amorae's web cookie + `web_consent` row are
the LIVE gate; this is the cross-device account record.

```
POST /api/v1/users/nsfw-consent
Headers: X-Amorae-Secret
Body:    { "user_id": "...", "source_ip": "1.2.3.4", "surface": "web_spicy" }
200 → any 2xx is success. Failures are logged + swallowed by amorae (a v2
      outage must not block the gate).
```
Backing table = migration `045_user_nsfw_consent` (dev session's PR).

## 3. Context seed — one-time SFW read (SHAPE NEEDS YOUR CONFIRMATION)
At the first web message in a new thread, amorae reads recent SFW app
history so web-Tara "remembers" (§4.2). Read-only; adult replies are NEVER
written back to v2.

```
GET /api/v1/spicy/context?user_id=<>&bot_handle=<>&limit=<N>
Headers: X-Amorae-Secret
200 → { "messages": [ { "role": "user"|"assistant", "content": "..." }, ... ] }
      oldest-first, SFW only, capped at `limit` (amorae sends 20).
```
**This is the one shape I guessed** — path, params, and response are a
proposal. Tell me the real endpoint and I'll conform `v2_client.read_recent_context`.

## 4. (Later) still-active ping — NOT built yet
§4.8 optional tame ping (amorae→v2) so v2's nudge engine counts spicy
activity. Deferred to a fast-follow; no shape proposed yet.

## What amorae does NOT need from v2
- No adult message storage (that's `amorae_db`, Level-2).
- No raw JWT, ever. No direct `amorae_db` access from v2.

## Status
amorae's client is written against §1–§3 above and smoke-tested with mocks.
Ready to integration-test against real v2 endpoints whenever they land.
