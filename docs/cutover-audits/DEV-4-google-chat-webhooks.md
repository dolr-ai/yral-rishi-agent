# DEV-4 — Google Chat admin webhooks port audit (21α.C5)

## TL;DR

**🟢 GREEN** — v2 has line-for-line parity with chat-ai's Google Chat webhook service. 4 of 4 notification functions ported, all call sites wired in routes, `GOOGLE_CHAT_WEBHOOK_URL` set in production env.

## Evidence

### Function-for-function port

| chat-ai (Rust, `src/services/google_chat.rs`) | v2 (Python, `app/services/google_chat.py`) | Status |
|---|---|---|
| `send_message(text)` | `async def send_message(text)` | ✅ |
| `notify_influencer_banned(id, name)` | `async def notify_influencer_banned(id, name)` | ✅ |
| `notify_influencer_ban_failed(id, err)` | `async def notify_influencer_ban_failed(id, err)` | ✅ |
| `notify_influencer_unbanned(id, name)` | `async def notify_influencer_unbanned(id, name)` | ✅ |
| `notify_influencer_unban_failed(id, err)` | `async def notify_influencer_unban_failed(id, err)` | ✅ |

5 of 5 functions present. Each formats text → POSTs `{"text": "..."}` to `GOOGLE_CHAT_WEBHOOK_URL`. Identical wire shape.

### Call sites — same trigger points

**chat-ai** (`src/routes/influencers.rs`):
- 604: `notify_influencer_ban_failed` after ban-attempt error
- 611: `notify_influencer_banned` after successful ban
- 659: `notify_influencer_unban_failed` after unban-attempt error
- 666: `notify_influencer_unbanned` after successful unban

**v2** (`app/routes/influencers.py`):
- 381: `google_chat.notify_influencer_banned(...)`
- 386: `google_chat.notify_influencer_ban_failed(...)`
- 410: `google_chat.notify_influencer_unbanned(...)`
- 415: `google_chat.notify_influencer_unban_failed(...)`

Same 4 trigger points. ✅

### Env wiring

```
GOOGLE_CHAT_WEBHOOK_URL=<set, non-empty>
```

(Confirmed via `docker service inspect`. URL not printed for OpSec.)

### Message text format

Both produce identical text (e.g.):
```
AI Influencer banned
ID: <influencer_id>
Name: <display_name>
```

No JSON-card formatting; just plain text body. Matches what Google Chat webhooks accept as the simplest case.

### What it does NOT cover (intentional scope)

chat-ai's `google_chat.rs` is also intentionally limited to ban/unban triggers — there's NO coverage for:
- Error spikes
- Abuse reports (would be a separate `notify_abuse_report` if it existed)
- LLM cost overrun / Sentry-fired alerts
- Cluster health (Patroni election, node failure)

This matches v2. Both services are functionally minimal here — Google Chat webhook is a "moderator was active" signal channel, not a broad ops-alerts pipeline. Sentry handles the broader ops alerts elsewhere.

## Recommendation

**Cutover gate C5: GREEN.** Pure parity port; no action required. If you want richer admin alerts (error spike, cost overrun, cluster events), that's net-new design — out of scope for cutover, separate followup work.

## What I did NOT verify

- Live webhook fire (would need to trigger a ban from the admin endpoint with a real admin JWT — the JWT-gated `/admin/influencers/{id}` route). Easy to verify post-deploy by banning one of your own test bots and watching the Google Chat room.
- The webhook URL is valid + room exists + Google Chat hasn't expired the integration. Same way to verify as above.
