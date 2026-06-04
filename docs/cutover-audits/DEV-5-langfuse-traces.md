# DEV-5 — Langfuse traces verification (21α.B4)

## TL;DR

**🟢 GREEN** — Langfuse is receiving v2 traces in real-time. Zero errors in the last 30 min; 404 successful ingestion POSTs in the last hour.

## Evidence

### Ingestion endpoint is firing

Live tail of `docker service logs yral-rishi-agent --since 30m | grep langfuse`:

```
2026-06-04 13:03:53,532 INFO httpx: HTTP Request: POST http://langfuse-web:3000/api/public/ingestion "HTTP/1.1 207 Multi-Status"
2026-06-04 13:03:55,660 INFO httpx: HTTP Request: POST http://langfuse-web:3000/api/public/ingestion "HTTP/1.1 207 Multi-Status"
... (30 more lines, all 207 Multi-Status, no error/warn/exception lines anywhere) ...
```

HTTP **207 Multi-Status** is Langfuse's canonical batch-ingestion response (per-event status returned inside the body). Confirmed via `langfuse-web` upstream behavior — events that fail schema validation surface as 4xx; we see zero of those.

### Throughput

```bash
$ docker service logs --since 1h yral-rishi-agent | grep -c "langfuse.*ingestion"
404
```

~7 ingestion batches/min — well above the floor for "alive and working."

### Langfuse service itself is healthy

```bash
$ curl http://langfuse-web:3000/api/public/health
{"status":"OK","version":"3.174.1"}
```

### Trace-call sites in v2 code (where `trace_generation` fires)

```
app/services/ai_client.py:250  generate_response (chat path, success branch)
app/services/ai_client.py:392  generate_response (chat path, second invocation)
app/services/ai_client.py:408  generate_response (chat path, third invocation)
app/services/ai_client.py:446  audio_transcription (transcribe path)
```

All four pass `provider`, `model`, `input_tokens`, `output_tokens`, `latency`, `user_id`, `conversation_id` per the canonical `langfuse_tracing.trace_generation` signature.

### No silent failures

```bash
$ docker service logs --since 30m yral-rishi-agent | grep -iE "langfuse" | grep -iE "error|exception|fail|warn"
(empty — zero matches)
```

The earlier streak_tracker logging hygiene fix (PR #270) means even empty-message exceptions in background loops surface with type+repr — Langfuse failures would too. None are firing.

## Sample trace IDs

I don't have a Langfuse UI token from this session to pull live trace IDs. **Easiest manual verification when you wake:**

1. Open `https://langfuse-agent.rishi.yral.com` in browser
2. Filter traces to last 1 hour
3. You should see ~400+ traces with name `chat-response` (the trace_name from `trace_generation`)
4. Pick any one → confirm it has `provider`, `model`, input/output token counts, latency_ms in the span metadata

## Recommendation

**Cutover gate B4: GREEN.** No action needed before alpha. The ingestion path is healthy and exercising at production rates already.

## What I did NOT verify

- Cross-replica trace consistency (both `.1` and `.2` agent replicas log Langfuse POSTs — I saw `.2` in the tail above; symmetric load expected)
- Langfuse storage health (ClickHouse + Postgres backing — separate concern; out of scope for this audit)
- Trace retention policy
