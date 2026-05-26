# WALKTHROUGH — yral-rishi-agent-soul-file-library

> One-line purpose: **a step-by-step trace of one concrete action through the codebase, file-by-file.** Per B7 (Tier-3 reading depth) — connects the diagrams in DEEP-DIVE.md to actual source lines.

## ⭐ What this walkthrough traces

**Service startup + first incoming request.** That's the simplest end-to-end story the template can tell today (real endpoints land in later PRs). Follow the numbered steps; each one cites a file and the relevant lines.

## Step 1 — uvicorn loads `app.main:app`

`Dockerfile` ends with: `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.

uvicorn imports the `app.main` module. Python runs every top-level statement before the imported name `app` is available. That import side-effect chain is what makes the rest of this walkthrough work.

## Step 2 — Sentry initializes (module-load, BEFORE FastAPI app exists)

→ `app/main.py` calls `init_sentry()` near the top of the module.
→ `app/sentry_middleware.py` reads `SENTRY_DSN` from the environment. If empty, no-op; if set, calls `sentry_sdk.init(dsn=..., environment=..., send_default_pii=False, traces_sample_rate=0.1)`.

WHY before the FastAPI object exists: the SDK hooks into Starlette's exception-handling machinery at `init()` time. Hooks added AFTER the app exists miss any startup-time exception.

## Step 3 — Langfuse initializes (same module-load pattern)

→ `app/main.py` calls `init_langfuse()`.
→ `app/langfuse_middleware.py` reads `LANGFUSE_TRACING_ENABLED` + the public/secret key pair + the host. No-ops when disabled or when keys are empty.

## Step 4 — structured logging is configured

→ `app/main.py` calls `configure_logging()`.
→ `app/logging.py` wires structlog with three processors in order: `_inject_request_id` (stamps the ContextVar's value), `_redact_disallowed_fields` (per H6 — scrubs anything not on `_FIELD_ALLOWLIST`), then standard structlog bookkeeping + final renderer (JSON in production, ConsoleRenderer locally).

## Step 5 — FastAPI app is constructed

→ `app/main.py`: `app = FastAPI(title=..., version=..., lifespan=lifespan)`.

The `lifespan` callback is bound but not yet invoked. It runs once on the first request (startup half) and once on SIGTERM (shutdown half).

## Step 6 — `RequestIdMiddleware` is mounted

→ `app/main.py`: `app.add_middleware(RequestIdMiddleware)`.

Starlette's middleware ordering is LIFO: the last `add_middleware` call sits OUTERMOST in the chain (first to see incoming requests, last to see outgoing responses). Today it's the only one we add; future PRs add more BEFORE this line.

## Step 7 — First request arrives

Mobile (or `curl`) sends an HTTPS request. It enters via the edge Caddy on rishi-1/2 (per C5 + C10), which reverse-proxies to rishi-4/5/6 over the public network; the Swarm-internal Caddy then routes via the `yral-v2-public-web` overlay to one of the 3 replicas of this service.

Inside the container, uvicorn hands the request to FastAPI's ASGI handler.

## Step 8 — `RequestIdMiddleware.dispatch` runs

→ `app/request_id_middleware.py`:
1. Reads `X-Request-ID` from headers; if absent, generates `str(uuid.uuid4())`.
2. Binds the value to `_request_id_var` (a `ContextVar`) via `.set()` — returns a token for resetting later.
3. Calls `sentry_sdk.set_tag("request_id", request_id)` (no-op when Sentry is disabled; otherwise lands on the per-request Sentry scope established by the FastAPI integration).
4. `await call_next(request)` — passes the request to the next middleware / handler.
5. On the response, sets `response.headers["X-Request-ID"] = request_id`.
6. In `finally`, calls `_request_id_var.reset(token)` so the value doesn't leak to the next request served by the same worker.

## Step 9 — Handler runs (today: implicit FastAPI default for any path)

The template ships no routes today, so any non-`/openapi.json` request gets a 404 from FastAPI's default error handler. Future PRs add `/health/*` (per F9), then real endpoints.

## Step 10 — Response leaves the chain

ASGI walks the middleware stack in reverse. Response gets `X-Request-ID` echoed (Step 8 #5). uvicorn serializes + writes to the socket. Edge Caddy forwards to the caller.

## Step 11 — SIGTERM (graceful shutdown, eventually)

When Swarm rolls a deploy or scales down, the container receives SIGTERM. uvicorn (as PID 1 because of the Dockerfile's exec-form CMD) catches it, signals FastAPI to run the lifespan shutdown half: `flush_langfuse()` drains pending LLM traces (per `app/langfuse_middleware.py`), then uvicorn exits cleanly.

## RELATED FILES

- `DEEP-DIVE.md` — the visual companion (request flow diagram covers Steps 7-10).
- `READING-ORDER.md` — file numbering matches the order things execute in.
- `app/main.py` — the orchestrator file this walkthrough follows.
- `Dockerfile` — explains why exec-form CMD matters for SIGTERM (Step 11).

## Day-4 walkthrough — one `GET /composed-prompt` call

Trace of a single orchestrator → soul-file-library RPC call after the Day-4 PR lands.

1. **Orchestrator sends:** `GET /composed-prompt?influencer_id=33333333-...&user_segment=new` on the Swarm overlay `yral-v2-internal` (per C3 — no auth, no public exposure).
2. **Uvicorn ASGI hits FastAPI** → `app/main.py:lifespan` already ran `init_pool()` at process start so the asyncpg pool is ready (`app/database.py:_pool`).
3. **`RequestIdMiddleware`** sets an `X-Request-Id` header on the request scope (for log correlation).
4. **FastAPI route dispatch** → `app/api/composed_prompt_routes.py:get_composed_prompt`. Pydantic parses query params; `user_segment="not-a-segment"` would have 422'd here, never reaching the handler body.
5. **Handler delegates:** `await compose(influencer_id, user_segment)` in `app/composer/four_layer_composer.py`.
6. **Composer Step 1** — `get_current(LAYER_PER_INFLUENCER, influencer_id)` via `app/repository/soul_file_repository.py`. Index-only scan through the partial unique index → ~0.1ms.
7. **Branch on L3:** None → `InfluencerSoulFileMissingError` → 404 at the route boundary. Has row → take `layer_3.archetype`.
8. **Composer Step 2** — three SELECTs for L1 / L2-by-archetype / L4. Any None → `SoulFileDataIntegrityError` → 500.
9. **Composer Step 3 — assembly:** `LAYER_SEPARATOR.join([l1.body, l2.body, l3.body, l4.body])`. Separator loaded at module-import from `shared-config.yaml:soul_file_library.layer_separator` (`\n\n---\n\n`). NO timestamps, UUIDs, dates inside the prompt — byte-identity is the load-bearing contract.
10. **Composer Step 4 — version pin:** `sha256(f"{l1.version}:{l2.version}:{l3.version}:{l4.version}")[:16]`.
11. **Return `ComposedPromptResponse{ layered_prompt, version_pin, cache_hit=False }`.**
12. **Route serialises to JSON** + 200 response. RequestIdMiddleware adds the response header on the way out.
13. **Orchestrator receives** the response, hands `layered_prompt` to the LLM provider as the cache-eligible prefix, appends the per-turn user message + memory facts as the un-cached suffix.

## Status

Day-4 walkthrough current. Day-5+ adds: provider `cache_control` markers around `layered_prompt` (orchestrator's concern); Redis cache promote in step 6 (`cache_hit=True` short-circuit before DB hit).
