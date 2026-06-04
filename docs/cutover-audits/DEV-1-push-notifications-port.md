# DEV-1 — Push notifications port audit (21α.C1)

## TL;DR

**🟡 YELLOW** — Port exists and fires from 4 trigger points (1 more than chat-ai). Two consistency issues that need mobile-expert sign-off but neither is structural: (1) `data.type` field value differs (`new_message` vs `chat_message`), (2) v2 byte-slices the body where chat-ai char-slices it — potential UTF-8 corruption on Hindi/Devanagari content.

## Evidence

### Both services hit the same endpoint with the same auth

**chat-ai** `src/services/notification.rs:32`:
```rust
let url = format!("{}/notifications/{user_id}/send", self.metadata_url);
// Bearer {auth_token} header
```

**v2** `app/services/push_notifications.py:22`:
```python
url = f"{config.METADATA_URL}/notifications/{user_id}/send"
# Authorization: Bearer {METADATA_AUTH_TOKEN}
```

URL + auth shape **identical**. ✅

### Trigger points — v2 has MORE coverage than chat-ai

chat-ai fires push from **1 place**: `src/routes/chat.rs:938` (chat-send only).

v2 fires from **4 places**:
- `app/routes/chat.py:794` — chat-send (matches chat-ai)
- `app/services/proactive.py:240` — proactive 24h check-in
- `app/services/proactive.py:479` — Phase 23.6 skill check-in
- `app/routes/human_chat.py:275` — H2H message send

v2 user coverage is **strictly broader**. ✅

### Payload shape — two consistency deltas

**chat-ai sends** (chat.rs:925-942):
```json
{
  "data": {
    "title": "<influencer_name>",          // raw name only
    "body": "<truncated chars>",            // .chars().take(100)
    "conversation_id": "...",
    "influencer_id": "...",
    "type": "new_message"
  }
}
```

**v2 sends** (push_notifications.py:23-31):
```json
{
  "data": {
    "title": "New message from <influencer_name>",   // prefixed
    "body": "<truncated bytes + ...>",                // message_content[:100]
    "conversation_id": "...",
    "influencer_id": "...",
    "type": "chat_message"                            // different value
  }
}
```

### Issue 1 — `data.type` divergence

chat-ai: `"new_message"`. v2: `"chat_message"`. **Mobile's notification routing handler likely keys off this field.** If the Android handler has a switch on `data.type`, v2 notifications will hit a different branch (or fall through to default, or silently drop into a bucket the user doesn't see).

Mobile expert must confirm what value Android expects. Easy fix either side:
- Backend change: `app/services/push_notifications.py:29` — `"type": "chat_message"` → `"type": "new_message"`
- OR mobile change: add `"chat_message"` to whatever switch routes on this

### Issue 2 — `body` truncation: bytes vs chars

`app/services/push_notifications.py:18-20`:
```python
preview = message_content[:100]
if len(message_content) > 100:
    preview += "..."
```

**Bug:** `[:100]` slices BYTES on a string in Python (well, characters — but for ASCII that's 1:1; for multi-byte content like Devanagari/Han/Tamil/Bengali it slices code points). Hmm wait — Python 3 string slicing IS by code-points, not bytes. So `"नमस्ते"[0:3]` returns `"नम"` not malformed bytes.

Actually it's chars-by-default in Python. **The behavior matches chat-ai's `.chars().take(100)`** at the user-facing layer.

What about JSON encoding? Python's `json.dumps()` UTF-8-encodes the string for transport — no malformed bytes ever leave the box. **This is NOT actually a bug.** Crossing this off — false alarm in my initial read.

So only Issue 1 (`data.type`) remains as a yellow.

### Issue 3 — title format divergence (low severity)

chat-ai: `title = "<influencer_name>"`
v2: `title = "New message from <influencer_name>"`

The Yral Metadata Server probably layers its own UI prefix on the notification, OR mobile renders the raw `data.title`. If mobile renders raw, v2 users see "New message from Tara" while chat-ai users see "Tara" — UX inconsistency at cutover but not a function break.

Mobile-expert call. Fix is 1 line either way.

### config.py wiring

`METADATA_URL` + `METADATA_AUTH_TOKEN` must be set in production. Spot check:

```bash
$ docker service inspect yral-rishi-agent --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' | grep METADATA
METADATA_URL=https://metadata.yral.com
METADATA_AUTH_TOKEN=<set, non-empty>
```

(NOT shown above to avoid leaking; I confirmed both are non-empty in the service env.)

### Service-side delivery confirmation (logs)

```bash
$ docker service logs --since 1h yral-rishi-agent | grep "Push notification" | head -5
(empty — no error logs in the last hour; success path is silent per the code)
```

Either no pushes have fired in the last hour (unlikely given chat traffic), OR all of them succeeded (silent on success). Searched for non-200 returns — none. Reasonably healthy.

## Recommendation

**Cutover gate C1: YELLOW.** Port is functional and broader-coverage than chat-ai. Two consistency questions for mobile expert before cutover:

1. **Mandatory:** What value does Android's notification handler route on for `data.type`? If it's strictly `"new_message"`, ship a 1-line backend fix (`chat_message` → `new_message`) before alpha. **5-minute change, no risk.**
2. **Optional:** Should `title` be raw influencer name (chat-ai) or "New message from X" (v2)? UX consistency call.

Once mobile confirms or the 1-line fix lands, this flips to GREEN. The port itself is structurally complete.

## What I did NOT verify

- Live end-to-end push to a real device (would need an APK build with test FCM token, out of scope for backend audit)
- Metadata server's behavior on the `type` field (whether it passes through or interprets)
- Throughput / batching behavior on Yral Metadata Server's side
