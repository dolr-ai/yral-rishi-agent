# SSE streaming protocol — Phase 2.7

The streaming chat endpoint emits Server-Sent Events (SSE) as the AI reply
generates word-by-word. Designed for `EventSource` clients and parity with
the non-streaming `POST /messages` contract.

## Endpoint

```
POST /api/v1/chat/conversations/{conversation_id}/messages/stream
Authorization: Bearer <JWT>
Content-Type: application/json
Accept: text/event-stream

{
  "content": "Hey, how's your day?",
  "message_type": "text",
  "media_urls": null
}
```

Backend feature-flag: `ENABLE_SSE_STREAMING` (default `TRUE`). When `FALSE`,
the endpoint returns `404`.

## Response

`Content-Type: text/event-stream` with three event types: `token`, `done`,
`error`. The connection closes after `done` or `error`.

### `event: token`

Streams one chunk of the AI reply text. Multiple `token` events arrive over
the lifetime of the stream; clients should append `data.text` to a buffer
and render incrementally.

```
event: token
data: {"text": "Hello! "}
```

### `event: done`

Final event on successful completion. Carries the persisted
`assistant_message` (same shape as the non-streaming `SendMessageResponse.
assistant_message`), provider info, and token count.

```
event: done
data: {
  "assistant_message": {
    "id": "01J5...",
    "conversation_id": "c-abc-123",
    "role": "assistant",
    "content": "Hello! How can I help today?",
    "message_type": "text",
    "media_urls": null,
    "audio_url": null,
    "audio_duration_seconds": null,
    "token_count": 12,
    "created_at": "2026-05-29T12:34:56.789Z"
  },
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "tokens": 12
}
```

When the content-safety pre-check intercepts the message (crisis / NSFW
filter), a single `token` event with the override response is followed by
`done` with `provider: "content_safety"` and `blocked: true`. No call is
made to the LLM.

### `event: error`

Terminal event on failure. Mirrors the non-streaming `error` object shape
(Phase 3.8) so clients can share rendering logic with the legacy endpoint.

```
event: error
data: {
  "code": "BLOCKED_CONTENT",
  "message": "I can't reply to that — try asking me something else.",
  "retryable": false
}
```

Codes: `BLOCKED_CONTENT` (Gemini safety / policy block, not retryable),
`TRANSIENT` (network / timeout, retryable), `NO_PROVIDER` (no Gemini key,
not retryable — should not happen in production).

## Client behavior recommendations

- Render `token.text` incrementally for a typewriter effect.
- On `done`, replace your in-progress buffer with `assistant_message.content`
  (server-side truth — handles any post-processing).
- On `error`, render `error.message` inline. If `retryable: true`, show a
  retry button on the user's message; if `false` (e.g. `BLOCKED_CONTENT`),
  consider a "rephrase" hint.
- If the stream closes without `done` or `error`, treat as `TRANSIENT`.

## Backward compat

The non-streaming `POST /messages` endpoint is unchanged. Mobile chooses
whether to use the streaming or legacy endpoint per turn — no flag exchange
needed.

## What is NOT streamed today

- NSFW influencer turns. OpenRouter SDK streaming would need its own code
  path; the route is still callable but returns a `NO_PROVIDER` error event
  for `is_nsfw=TRUE` conversations until that path is added.
- Image-generation turns. The text portion would be just the prompt input
  to the image model; clients should use `POST /images` for image-aware
  generation.
- Audio transcription turns. Streaming transcription is a separate spec.
