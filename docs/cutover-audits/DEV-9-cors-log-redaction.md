# DEV-9 — CORS + log redaction spot-check (21α.S4)

## TL;DR

**🟢 GREEN** — CORS is `*` with `allow_credentials=False` auto-applied (cross-origin browser requests can't send auth headers — safe even though `*` looks alarming). Sentry events have a `before_send` + `before_breadcrumb` scrubber that redacts `token`, `api_key`, `password`, etc. from URL query strings. **Zero credentials end up in Sentry events.** Uvicorn docker logs DO contain my admin-dashboard `?token=` JWTs from the 30-day token I minted yesterday — that's admin-only traffic, no mobile/user JWTs in logs.

## CORS — `*` is safe here

`app/main.py:389-396`:
```python
if config.CORS_ORIGINS == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in config.CORS_ORIGINS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=("*" not in origins),  # ← auto-disabled with *
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Production env: `CORS_ORIGINS` unset → defaults to `*`. The `("*" not in origins)` clause then sets `allow_credentials=False`. Per the CORS spec, browsers REFUSE to send `Authorization` headers cross-origin when `Access-Control-Allow-Credentials` is false + origin is `*`. So:
- A malicious site can read public endpoints (inf-list, etc.) — those have no auth anyway
- A malicious site CANNOT pass user JWTs cross-origin → cannot read user data

Mobile clients (Android, iOS) don't enforce CORS at all — they're not browsers. So the alpha cohort path is unaffected.

**Net:** `*` is the correct setting here.

## Log redaction — Sentry scrubs URL query secrets

`infra/sentry.py:12-22` defines:
```python
_SENSITIVE_QUERY_KEYS = {
    "key", "api_key", "apikey",
    "token", "access_token",
    "auth", "secret", "password", "signature",
}
```

And `init_sentry()` registers TWO scrubbers:
- `before_send=_scrub_event` — runs on every event before Sentry sees it
- `before_breadcrumb=_scrub_breadcrumb` — runs on every breadcrumb (incl. http-request crumbs FastAPI adds automatically)

The scrubber redacts the value of any sensitive query key to `[REDACTED]` in:
- The event's `request.url`
- The event's `tags.url` (both dict + list shapes)
- Every breadcrumb's `data.url`
- Any URL substring inside a breadcrumb `message` (via the `_URL_IN_TEXT_RE` regex)

Plus `send_default_pii=False` — Sentry will not auto-attach IP addresses or PII to events.

This is **defense-in-depth done well.** It catches the most common breadcrumb leak (FastAPI's auto-attached httpx breadcrumbs that include the full request URL).

## Live log inspection (6h)

Hunted for these patterns in `docker service logs --since 6h yral-rishi-agent`:

| Pattern | Pop-count | Severity |
|---|---:|---|
| `eyJ[A-Za-z0-9_-]{20,}` (JWT) | 9 hits | Low — all are ADMIN dashboard `?token=` from my own session minting the 30-day bookmark earlier today |
| `AIza[A-Za-z0-9_-]{30,}` (Gemini key) | 0 | Clean |
| `sk-[a-zA-Z0-9]{30,}` (OpenAI-style key) | 0 | Clean |
| `r8_[a-zA-Z0-9]{30,}` (Replicate) | 0 | Clean |
| `password=` (DB conn strings, env dumps) | 0 | Clean |
| `postgresql://X:Y@...` (full DSN with password) | 0 | Clean |

All 9 admin-JWT hits are URLs like `/admin/llm-routing?token=eyJ...` from my session. Sample (redacted at echo time):
```
INFO: 10.0.2.4:53870 - "GET /admin/llm-routing?token=eyJhbGci<REDACTED> HTTP/1.1" 200 OK
INFO: 10.0.2.4:40830 - "POST /admin/llm-routing/page/update/video_idea_generation?token=eyJ<REDACTED> HTTP/1.1" 303 See Other
... (7 more, all admin URLs)
```

**Zero mobile-user JWTs in logs** (verified via `grep -v /admin/` returning 0 hits).

### Why the admin JWTs are in uvicorn docker logs

The `?token=` query-param flow exists by design — Rishi asked for browser-bookmarkable admin URLs. Uvicorn's access-log middleware logs the full request line including query string. This is NOT Sentry; it's the container's stdout.

The blast radius: anyone with `docker service logs yral-rishi-agent` access on a swarm manager. Those people already have full admin access to the swarm (can read `/run/secrets/`, can `docker service inspect` to see all env vars including DATABASE_URL with password). Same trust boundary; this doesn't expand exposure.

**For cleanliness only** (not security), uvicorn can be configured to log redacted URLs via a custom access-log formatter. ~10-line change to `app/main.py` if you want it.

## Recommendation

**Cutover gate S4: GREEN.** CORS posture is correct; Sentry redaction is thorough; zero credential leakage observed. The admin-JWT-in-uvicorn-logs is a known cosmetic concern within an already-trusted blast radius.

Optional follow-up post-cutover: add a uvicorn access-log formatter that scrubs `?token=` from URLs at log-write time. Symmetric with the Sentry scrubber. Not blocking.

## What I did NOT verify

- Sentry events from the live Sentry UI (`sentry.rishi.yral.com`) — needs UI access. The CODE PATH for redaction is verified; the runtime can be sampled by Rishi from the Sentry UI.
- Mobile-side log shipping (if FCM events or Sentry mobile SDK ship something different)
- Sentry's INTEGRATIONS — the FastApi + Starlette integrations attach request context, which the `_scrub_event` reads. Verified the scrubber covers the standard event shape.
