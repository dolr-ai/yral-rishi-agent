# Session 2 LOG — Template & Hello-World
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

---

## 2026-05-13 — Day 3, PR 2b (3 B7-new doc scaffolds)

**Branch:** `session-2/f8-walkthrough-glossary-lost-docs` (off main with PR #34 merged)

Closes out the 8-doc F8 requirement. PR #34 landed the 5 originals (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY); this PR adds the 3 B7-new docs.

**Files added (3):**
- `yral-rishi-agent-new-service-template/WALKTHROUGH.md` — 11-step narrative trace of "service startup + first request" through the codebase. Each step cites the file(s) involved + the why. Covers uvicorn module-load → Sentry init → Langfuse init → logging config → app construction → middleware mount → first request through `RequestIdMiddleware.dispatch` → response leaves → SIGTERM graceful shutdown. 81 lines.
- `yral-rishi-agent-new-service-template/GLOSSARY.md` — alphabetical table of 28 domain terms with plain-English definitions. Each entry references the relevant CONSTRAINTS row (or the file where the term lives in code) when applicable. Covers: Allowlist / asyncpg / Caddy / Canary deploy / ContextVar / GHCR / Idempotency key / JWKS / Langfuse / Lifespan / Loki / Middleware / Multi-stage build / No-op / Overlay network / Patroni / pgBouncer / pydantic / Replica / Sentinel / Sentry / Service tag / Singleton / Soul File / Stateful core / structlog / Swarm / Swarm secret / Synthetic user / uvicorn. 51 lines.
- `yral-rishi-agent-new-service-template/WHEN-YOU-GET-LOST.md` — restaurant analogy + "Are you trying to ___?" pointer table + "fastest path back to productive" recovery procedures (>1 day away → state/log; >1 week away → README/DEEP-DIVE/WALKTHROUGH path). Per B7 the "restaurant/pantry analogy + north-star orientation" is the spec; delivered. 73 lines.

**Total diff ~205 lines.** Coordinator estimated ~120; reality came in higher because GLOSSARY's table-with-definitions format is naturally line-heavy. Still well under Codex's truncation threshold.

**Decisions made (worth recording):**
- **WALKTHROUGH traces "startup + first request"** — the simplest concrete action the template can tell end-to-end today (no real endpoints yet). The 11-step structure stays once real endpoints land; the steps just get more detail.
- **GLOSSARY is alphabetical, not topic-grouped.** ADHD-friendly per Rishi — you scan alphabetically without needing to know which "topic bucket" a term belongs to.
- **Each GLOSSARY entry cites a CONSTRAINTS row OR the code file where the term lives.** Lets a reader trace from definition → enforcement point.
- **WHEN-YOU-GET-LOST uses the restaurant analogy** per B7 spec. Kept the analogy tight (5 mappings) + added a "when the analogy breaks" section so a reader doesn't take the metaphor too literally.
- **WHEN-YOU-GET-LOST includes both "1-day away" and "1-week away" recovery paths.** Different mental contexts; different reads.
- **Avoided over-using `config`** in light of coordinator's heads-up on the not-yet-formal B2 status. Used "configuration" or other phrasings where natural.

**B7 compliance:** every doc carries the one-line purpose at top, ⭐ START HERE section, concrete CONSTRAINTS citations, RELATED FILES footer, `## Status: Scaffold` marker noting "real content Days 5-6 per role spec".

**Constraints honored:** A2.1 (lean scaffolds, no speculative content), B7 (3-tier reading flow + restaurant analogy on WHEN-YOU-GET-LOST per the explicit spec), F8 (CORRECT 3 of 8 doc names this time; coordinator-verified after the PR #32 lesson).

**Carve-outs used:** B2 PR #31 (`ci`), PR #26 (`init`), PR #24 (`app`).

**Next:** PR 3 — `session-2/new-service-sh`: `scripts/new-service.sh` 1-command spawner. Then PR 4 (D8 bridge scripts) + PR 5 (spawn hello-world).

---

## 2026-05-13 — Day 3, PR 2a REDO (F8-compliant 5-doc scaffolds)

**Branch:** `session-2/f8-compliant-doc-scaffolds` (off main; PR #32 closed + branch deleted)

**Why the redo:** PR #32 shipped the 5 docs as README/ARCHITECTURE/RUNBOOK/ONBOARDING/TROUBLESHOOTING based on a coordinator-message list. Codex flagged it. Per CONSTRAINTS F8 row + CURRENT-TRUTH.md + Rishi's 2026-04-27 doc-set decision, the locked names are: **DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY** (5 originals) + WALKTHROUGH / GLOSSARY / WHEN-YOU-GET-LOST (3 new). Coordinator + Codex caught coordinator drift; redo cost a full PR cycle. Lesson: cross-check CONSTRAINTS.md row text on any coordinator citation BEFORE writing the code. Logging here so future-me reads this entry first when CONSTRAINTS citations come up.

**Files added (5):**
- `yral-rishi-agent-new-service-template/DEEP-DIVE.md` — visual walkthrough with 4 ASCII diagrams (request flow, deploy flow, DB HA, network topology). Each diagram is annotated with the relevant CONSTRAINTS row. 152 lines.
- `yral-rishi-agent-new-service-template/READING-ORDER.md` — numbered table (25 rows) of every file in reading order. Each row has ETA + priority (🟥 HIGH / 🟨 MED / ⬜ LOW) + 1-line "why you read it". Total budget table at the bottom. 55 lines.
- `yral-rishi-agent-new-service-template/CLAUDE.md` — instructions for AI agents working in this service (Claude Code + Codex). Sections: ⭐ If you only read one section (A2.1), service identity, constraints to cite, when-asked-to-add-new-module, when-asked-to-modify-main.py, when writing tests, when writing CI, when Codex flags something, **cross-check coordinator's constraint citations** (closes the loop on this PR's redo). 85 lines.
- `yral-rishi-agent-new-service-template/RUNBOOK.md` — operating procedures. ⭐ START HERE if it's an incident, deploy + rollback per I2/I3, common operations table, P0/P1/P2 severity, replicas crash-looping section, slow-requests section, monitoring list (Sentry/Langfuse/Grafana/Uptime Kuma/Google Chat per D6), backups. 92 lines.
- `yral-rishi-agent-new-service-template/SECURITY.md` — threat model. 7 load-bearing security properties up top, then sections: authentication (E6/E9), authorization (F3 schema isolation), secrets (D1+D8 with the 5-inheritance-secrets table), PII (H6 + send_default_pii=False), prompt injection (H5), network isolation (C3+C10), out-of-scope threats, who-to-call. 99 lines.

**Total diff ~483 lines.** Over the <200 line target but well under Codex's truncation threshold (~800). Matches coordinator's "5 originals first" split. DEEP-DIVE is the heaviest at 152 lines (ASCII diagrams are line-heavy by nature).

**Decisions made (worth recording):**
- **Skipped the README placeholder cleanup.** Per coordinator: README is GitHub convention, not F8. If genuinely needed, fold into a separate note. The placeholder is currently fine; cleaning it can happen Days 5-6 alongside real content. Keeps this PR strictly F8-scoped.
- **DEEP-DIVE diagrams use box-drawing characters.** Renders cleanly in GitHub markdown + any plain-text viewer. No mermaid dependency.
- **READING-ORDER uses emoji priority markers** (🟥/🟨/⬜). Matches the existing project voice (MASTER-STATUS.md uses ⭐/🚦/📊/🤖 liberally; agent docs use ⭐ START HERE markers throughout). ADHD-friendly per Rishi.
- **CLAUDE.md includes a "cross-check coordinator's constraint citations" section.** Captures the lesson from this PR's redo so future AI agents working here don't repeat it.

**B7 compliance:** every doc carries the one-line purpose at top, ⭐ START HERE section, concrete constraint citations, RELATED FILES footer, `## Status: Scaffold` marker noting "real content Days 5-6".

**Constraints honored:** A2.1 (no README cleanup folded in; scope discipline), B7 (3-tier reading flow on every doc), F8 (CORRECT 5 of 8 doc names this time — DEEP-DIVE/READING-ORDER/CLAUDE/RUNBOOK/SECURITY), I6 (closed-loop on the redo — pushed back on the wrong list once it was clear, accepted coordinator's correct list, logged the lesson).

**Carve-outs used:** B2 PR #31 (`ci`), PR #26 (`init`), PR #24 (`app`).

**Next:** PR 2b — `session-2/template-three-b7-doc-scaffolds`: WALKTHROUGH + GLOSSARY + WHEN-YOU-GET-LOST scaffolds. That set was correct in the original plan.

---

## 2026-05-13 — Day 3, PR 1 (per-service CI workflow template)

**Branch:** `session-2/ci-workflow-template` (off main with PR #28 merged)

**Files added (1):**
- `yral-rishi-agent-new-service-template/.github/workflows/per-service-ci.yml` — workflow-template file (NOT a live workflow — GitHub Actions only auto-discovers at root `.github/workflows/`, not under subdirectories). Defines two jobs: `lint` (byte-compiles every .py in `app/` with stdlib `py_compile`) and `docker-build` (multi-stage build from `Dockerfile`, no GHCR push yet). Path-scoped to `yral-rishi-agent-new-service-template/**`; new-service.sh sed-substitutes the path + workflow name when spawning. 116 lines including full B7 doc structure.

**Scope decision worth recording:**
- My agent definition forbids writes to `.github/workflows/` (coordinator-only per I9). Day 3's "CI workflows" item runs into that scope cap. Resolution: ship the WORKFLOW TEMPLATE inside the template folder (`yral-rishi-agent-new-service-template/.github/workflows/per-service-ci.yml` — in scope per my path prefix). Coordinator installs at root for the template itself (one-shot, `template-ci.yml`); spawned services get their per-service workflow generated by new-service.sh (PR 3).
- Per the agent spec, that means I CAN'T fire any CI from this PR until coordinator copies the template to root. Coordinator can do this lazily — the template file is the source of truth.

**Decisions made (worth recording):**
- **Workflow template lives at `yral-rishi-agent-new-service-template/.github/workflows/per-service-ci.yml`.** Path doesn't trigger GitHub Actions auto-discovery (good — we don't want it firing as a real workflow with template placeholders). But the path is intuitive for a future contributor looking for "this service's CI". File header makes the "this is a template, not a workflow" point explicit.
- **Jobs: lint + docker-build only.** A2.1 — skipping pytest (no tests yet), validate-secrets (script lands PR 4), GHCR push (deploy workflow is a later concern). Each job is the minimum useful gate.
- **`docker/build-push-action@v6` with `push: false`.** Builds the image as a verification step without pushing. GHCR push lands when deploy workflow lands.
- **GitHub Actions cache via `cache-from: type=gha`.** Speeds up subsequent CI runs significantly without adding any account-level setup.
- **`py_compile` over `ruff` for lint.** ruff isn't in pyproject.toml yet (per A2.1 J1 ramp-up — adding when a test suite needs it). py_compile is stdlib, zero setup, catches syntax errors. ruff slots in as an additional step when it lands.

**B7 compliance:** file carries the full header (⭐ START HERE), section comments per job explaining role + rationale, RELATED FILES footer pointing at the upstream/downstream files.

**Constraints honored:** A2.1 (minimal jobs, defer non-essential gates), B7 (full doc structure), F12 (Python 3.12), F13 (GHCR — referenced but not pushed yet), F16 (monorepo path-scoped CI), I9 (left root `.github/workflows/` for coordinator).

**Coordinator-side follow-up:** install the template's analogue at root `.github/workflows/template-ci.yml` (with paths scoped to `yral-rishi-agent-new-service-template/**`) so changes to the template folder itself get CI'd. Or wait for new-service.sh to land in PR 3 + have it emit the workflow content as part of spawn.

**Next:** PR 2 — 8 required doc scaffolds per F8 (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY / WALKTHROUGH / GLOSSARY / WHEN-YOU-GET-LOST). Initial content fills in Days 5-6 per role spec.

---

## 2026-05-13 — Day 2, PR 4 (config loader)

**Branch:** `session-2/config-loader` (off main with PR #27 merged)

**Files added (1):**
- `yral-rishi-agent-new-service-template/app/config.py` — typed pydantic-settings `Settings` model + cached `get_settings()` singleton. Wraps the env vars currently used by sentry / langfuse / logging modules: `environment`, `log_level`, `sentry_dsn`, `sentry_service_tag`, `sentry_traces_sample_rate`, `langfuse_tracing_enabled`, `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`. case_sensitive=False so `SENTRY_DSN` or `sentry_dsn` both match. 124 lines including B7 docs.

**Files modified (1):**
- `yral-rishi-agent-new-service-template/pyproject.toml` — added `pydantic-settings==2.7.1`.

**Total diff ~129 lines**, well under the <200 target. Single concern this time (no bundling).

**Decisions made (worth recording):**
- **Env-only loading; shared-config.yaml integration deferred.** Per A2.1 the YAML loader lands when the first consumer needs nested structured data (e.g. Redis Sentinel hosts list per C11). Adding pyyaml + a merge layer before any consumer needs it would be over-engineering. Documented in the file header as a clear "next-up" note.
- **`functools.lru_cache(maxsize=1)` for the singleton.** Stdlib, dependency-free, exactly the right shape. Avoided rolling a manual module-level `_settings = None` + getter pattern.
- **Existing middleware NOT refactored to use Settings in this PR.** They still read env directly. The Settings class is available for future modules (database, redis client, LLM client). Migrating existing modules is a follow-up if needed. Keeps this PR focused.
- **`extra="ignore"` in SettingsConfigDict.** Avoids Settings construction failing when the env has unrelated vars (PATH, PWD, hundreds of `GITHUB_*` in CI, etc.).
- **No nested submodel structure.** Flat `sentry_dsn` instead of `sentry.dsn` — matches the actual env var shape and keeps the single-underscore convention. Nesting can come later if a consumer naturally wants `settings.sentry.dsn` access.

**B7 compliance:** file carries the file header (with ⭐ START HERE), class + function WHAT/WHEN/WHY blocks, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** A2.1 (no speculative YAML loader, no premature refactor of existing modules), B1 (every name reads as English), B2 (`config` is an allowed abbreviation in B2's original list; `init` carve-out also active), F12 (Python 3.12 + pydantic-settings 2.x), C7 (path forward documented — shared-config.yaml loader is where YAML lands).

**Codex false-positive note (coordinator-flagged on PR #27):** Codex hallucinated a `per-req` NIT when the diff said `per-request`. Same pattern as the earlier `app` BLOCKER hallucination. Watching for similar truncation-fail-closed inventions on future PRs; will read Codex comments against the actual diff before acting on them.

**Next:** Day 2 middleware skeleton complete after PR 4 merges. Day 3 work begins: CI workflows + 8 docs + new-service.sh + spawn hello-world. J1-J6 testing pyramid starts mattering here.

---

## 2026-05-13 — Day 2, PR 3 (request-ID middleware + structured logging, bundled per coordinator)

**Branch:** `session-2/request-id-and-logging` (off main with PR #25 merged + PR #26 broadened B2 carve-out for `init`)

**Files added (2):**
- `yral-rishi-agent-new-service-template/app/request_id_middleware.py` — `RequestIdMiddleware` (Starlette BaseHTTPMiddleware) + module-level `ContextVar` + `get_request_id()` accessor. Reads `X-Request-ID` or mints UUID4 per request, binds to ContextVar for the request's lifetime, tags the Sentry scope, echoes the header on the response. 91 lines.
- `yral-rishi-agent-new-service-template/app/logging.py` — structlog config + two custom processors. `_inject_request_id` stamps the ContextVar's value onto every log line; `_redact_disallowed_fields` redacts any field key NOT on `_FIELD_ALLOWLIST` per H6 (allowlist not denylist — the H6-correct interpretation). 22-field allowlist covers structlog built-ins, request-scoped IDs, service identity, HTTP shape, opaque user IDs, error classification, LLM telemetry. JSON renderer for staging/production, ConsoleRenderer locally. 114 lines.

**Files modified (2):**
- `yral-rishi-agent-new-service-template/app/main.py` — imports + module-load `configure_logging()` call + `app.add_middleware(RequestIdMiddleware)` after app creation. Comment notes that `add_middleware` is LIFO so RequestIdMiddleware (added last) runs OUTERMOST.
- `yral-rishi-agent-new-service-template/pyproject.toml` — added `structlog==24.4.0`.

**Total diff ~240 lines.** Over the <200 target but coordinator bundled two concerns into one PR, so the size was implicit.

**Decisions made (worth recording):**
- **ContextVar over `request.state`.** ContextVar propagates across `await` boundaries; `request.state` requires a reference to the FastAPI Request object that background tasks + exception handlers don't have.
- **Mint UUID4 on missing header.** Don't depend on the edge Caddy stack setting the header — guarantees every log/trace has a request_id.
- **Allowlist redaction, not denylist.** H6 explicitly calls for an allowlist. Denylists miss any new field name nobody flagged; allowlists default-deny.
- **`_FIELD_ALLOWLIST` is conservative.** 22 fields covering structlog built-ins + the obvious safe-shape fields (HTTP method/path/status, opaque IDs, LLM telemetry shape). Per-service additions require a 1-line PR — small security review per field.
- **`add_middleware(RequestIdMiddleware)` LAST so it runs OUTERMOST.** Starlette's middleware ordering is LIFO; the last `add_middleware` call is the first to see incoming requests. Comment in main.py spells this out for the next person adding middleware.
- **`configure_logging()` runs AFTER Sentry/Langfuse init but BEFORE app creation.** Anything logged during app startup goes through the structured pipeline.

**Used name `app/logging.py`.** Risk: shadows stdlib `logging` module. Verified safe — Python's `import logging` always resolves to stdlib (absolute imports), and our file is only reachable via `from app.logging import ...`. B1-compliant name; A2.1-compliant choice (no clever renaming).

**B7 compliance:** both files carry the file header (with ⭐ START HERE), function WHAT/WHEN/WHY blocks, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** A2.1 (trimmed verbose B7 headers to fit closer to size budget), B1 (every name reads as English), B2 (`init` carve-out per PR #26 — `init_*` functions stay as written), F12 (Python 3.12 + asyncio-safe ContextVar), H6 (PII allowlist redaction in the log processor).

**Carve-outs used:**
- B2 + PR #24 — `app/` package name.
- B2 + PR #26 — `init_*` function names + this PR's reliance on existing `init_sentry()` / `init_langfuse()`.

**Next:** PR 4 (Day-2 plan PR 5 in the original sketch) — `session-2/config-loader`: `app/config.py` typed pydantic settings reading shared-config.yaml + env vars.

---

## 2026-05-13 — Day 2, PR 2 (Langfuse middleware)

**Branch:** `session-2/langfuse-middleware` (off main with PR #22 merged + PR #24 B2 carve-out)

**Files added (1):**
- `yral-rishi-agent-new-service-template/app/langfuse_middleware.py` — `init_langfuse()` + `get_langfuse()` + `flush_langfuse()`. Module-level singleton client `_client` (None until init). No-ops when `LANGFUSE_TRACING_ENABLED != "true"` OR when either of `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` is empty. Host from env with default `https://langfuse.rishi.yral.com` (per D4). 123 lines including full B7 doc structure.

**Files modified (2):**
- `yral-rishi-agent-new-service-template/app/main.py` — added module-load `init_langfuse()` call (mirrors Sentry pattern) + `flush_langfuse()` in lifespan shutdown so SIGTERM doesn't drop in-flight traces. ~17 lines added / ~4 modified.
- `yral-rishi-agent-new-service-template/pyproject.toml` — added `langfuse==2.59.7` to runtime deps. 5 lines added.

**Total diff ~145 lines**, well under <200 target.

**Decisions made (worth recording):**
- **init/get/flush trio, no auto-magic.** Langfuse is a client, not a hooked-in middleware — it only records when consumer code calls `client.trace(...)`. `get_langfuse()` is the only way LLM-client code (added later per A10) can fetch the singleton; without it, init does nothing useful. Not speculative.
- **No-op when keys are empty.** Default-deny so a half-configured environment still runs (just without traces) rather than crashes at startup. Matches Sentry's empty-DSN handling for consistency.
- **Default-deny on the LANGFUSE_TRACING_ENABLED flag.** Literal "true" required to enable; any typo (including "True", "TRUE", "1") evaluates to disabled. Safer than default-allow.
- **`flush_langfuse()` runs in lifespan shutdown, not on signal handler.** FastAPI's lifespan shutdown is the official SIGTERM hook; rolling our own signal handler would duplicate machinery.

**B7 compliance:** file carries the file header (with ⭐ START HERE), function WHAT/WHEN/WHY blocks for all three public functions, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** A2.1 (no speculative API surface — just what consumers need), D4 (host = langfuse.rishi.yral.com), D8 (keys via secrets.yaml.template), F12 (Python 3.12 + asyncio-compatible client).

**Carve-out used:** B2 + PR #24 — `app/` package name explicitly allowed.

**Next:** PR 3 — `session-2/request-id-middleware`: per-request UUID propagation via X-Request-ID, threaded into Sentry + Langfuse contexts.

---

## 2026-05-13 — Day 2, PR 1 (app/main.py + Sentry middleware)

**Branch:** `session-2/sentry-middleware`

**Files added (3):**
- `yral-rishi-agent-new-service-template/app/__init__.py` — package marker. 11 lines.
- `yral-rishi-agent-new-service-template/app/main.py` — minimal FastAPI app with no-op lifespan placeholder. Calls `init_sentry()` at module-load time BEFORE the FastAPI object is built so Sentry's exception hooks are in place for app startup too. Title + version are template placeholders; new-service.sh overwrites at spawn time.
- `yral-rishi-agent-new-service-template/app/sentry_middleware.py` — `init_sentry()` helper. Reads SENTRY_DSN + SENTRY_SERVICE_TAG + ENVIRONMENT env vars. No-ops when DSN is empty (local dev). traces_sample_rate=0.1 default. send_default_pii=False per H6.

**Files modified (1):**
- `yral-rishi-agent-new-service-template/pyproject.toml` — added `sentry-sdk[fastapi]==2.22.0` to runtime deps.

**Total diff: ~187 lines.** Targeting <200 per coordinator's "Codex APPROVE-clean rather than truncation-fail-closed" guidance.

**Decisions made (worth recording):**
- **Sentry inits at module-load, not in lifespan.** The FastAPI integration hooks into Starlette's exception handlers at `sentry_sdk.init()` time. The hook must be in place before app startup so exceptions during startup (DB pool init, etc.) are captured. Lifespan runs after the app exists — too late.
- **Empty DSN → no-op.** Local dev runs without a real Sentry project. Service still runs; we just don't report errors.
- **Lifespan is a no-op placeholder.** Reserves the structure so PRs 2–5 can plug in without renaming or touching main.py's signature.
- **Single module-level `app`, no factory.** uvicorn's `app.main:app` expects a module-level variable; factory pattern adds papercut without value.

**B7 compliance:** every file carries the file header (with ⭐ START HERE), function WHAT/WHEN/WHY blocks, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** A2.1 (lean — only the Sentry init helper, no speculative middleware classes/factories), A7 (DSN points at sentry.rishi.yral.com), D3 (service-tag stamping), F12 (Python 3.12 + FastAPI + asyncio), H6 (send_default_pii=False).

**Next:** PR 2 — `app/langfuse_middleware.py` on branch `session-2/langfuse-middleware`. Adds langfuse SDK dep + init helper following the same pattern.

---

## 2026-05-13 — Day 1, PR 3 (configs + secrets manifest) — rebased onto main after PR #18 merged

**Branch:** `session-2/template-skeleton-configs`

**Files added (4):**
- `yral-rishi-agent-new-service-template/project.config` — per-service single source of truth. Bash-sourceable KEY=value pairs (identity, Postgres SCHEMA/ROLE/CONNECTION_LIMIT per F3, Swarm STACK + IMAGE_REPO at GHCR per F13, Sentry service tag per D3, replica caps + REPLICA_COUNT=3 per G2, backup endpoint + bucket per D2 L3 row, three on/off feature flags).
- `yral-rishi-agent-new-service-template/shared-config.yaml` — cross-service shared values (per C7). YAML sections: sentry (host=sentry.rishi.yral.com per A7+C4), langfuse (host=langfuse.rishi.yral.com per D4), auth (jwks_url + cache + strict-validation default FALSE per E6/E9), billing (access_check_url + 60s cache per E7), database (pgbouncer + asyncpg statement_cache_size=0), redis (sentinel master + 3 sentinel hosts per C11), idempotency (default ON, 24hr TTL per F10), feature_flags (30s poll per F11), llm (default Gemini + NSFW OpenRouter per A10 + runaway cap 500 INR/day per E4), latency (max_p95_ratio=0.5 per E1, streaming first-token 200ms per E2).
- `yral-rishi-agent-new-service-template/secrets.yaml.template` — per-service secrets manifest per D8. Five inheritance secrets: DATABASE_URL, REDIS_SENTINEL_PASSWORD, SENTRY_DSN, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY. Each declared with full D8 schema. ${PROJECT_NAME} substitution for new-service.sh.
- `yral-rishi-agent-new-service-template/.env.example` — hand-written today to match secrets.yaml.template + 3 non-secret env vars (ENVIRONMENT, LOG_LEVEL, LANGFUSE_TRACING_ENABLED). Day 3 generator script will replace + drift-check via CI.

**Also modified (1):**
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md` — raised DEP-003 (Session 1 confirm 3 Swarm overlay names). Per coordinator (2026-05-13): resolves on Session 1's Day 4 finish; not blocking.

**Decisions made (worth recording):**
- **`project.config` stays bash-sourceable key=value, NOT YAML.** Matches existing infra-template's pattern and works with CI's `>> $GITHUB_ENV` parsing.
- **`secrets.yaml.template` with `.template` suffix.** new-service.sh copies + sed-substitutes it per spawn.
- **5 inheritance secrets, not more.** Service-specific secrets (JWT_JWKS_URL, OPENROUTER_API_KEY, etc.) get added per service. Keeps template minimal per A2.1.
- **`shared-config.yaml` lives per-service, not at umbrella root.** Per F16 monorepo: each spawned service has its own copy; CI lint (Day 3) verifies they all match the canonical template version.

**B7 compliance:** every file carries the file header (with ⭐ START HERE), section headers, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** A2.1, A7/C4, C3, C7, C11, D1/D8, D2/D3/D4, E1/E2/E4/E6/E7/E9, F3/F9/F10/F11/F13, G2, I2, I9, I11.

**Next:** idle until Day 2 kickoff. Day 2 plan = app-layer middleware (PR 4: app/main.py + health, PR 5: database + redis, PR 6: sentry + langfuse, PR 7: auth + idempotency + pii + prompt-injection, PR 8: llm_client + event_stream + feature_flags).

---

## 2026-05-13 — Day 1, PR 2 (compose files) — rebased onto main after PR #17 merged

**Branch:** `session-2/template-skeleton-compose`

**Files added (2):**
- `yral-rishi-agent-new-service-template/docker-compose.yml` — local dev stack. Service (built from local Dockerfile, port 8000 exposed, `--reload`, source mounted RO) + Postgres 17-alpine (port 5432 exposed, named volume) + pgBouncer 1.23.1 (bitnami image, session mode, port 6432 internal) + Redis 7-alpine (port 6379 exposed, appendonly). Langfuse intentionally left disabled via `LANGFUSE_TRACING_ENABLED=false` — full Langfuse stack is ~1GB of containers and the rishi-6 shared instance is the real-traffic destination per D4. A docker-compose profile for local Langfuse can be added later if a dev specifically asks (A2.1).
- `yral-rishi-agent-new-service-template/docker-compose.swarm.yml` — production Swarm stack. Service-only (cluster owns Postgres/Redis/pgBouncer/Langfuse). Image from GHCR. 3 replicas per G2. Rolling update parallelism=1, order=start-first, auto-rollback on failure (I2). Resource caps 1 CPU / 512 MiB / replica. Healthcheck against `/health/ready` (F9). Three external overlay networks per C3 (`yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`). Three external Swarm secrets (`database_password`, `redis_password`, `sentry_dsn`). Caddy auto-discovery labels for the edge stack.

**Decisions made (worth recording):**
- **Local Langfuse: env-disabled, no profile.** Per A2.1, defer the optional `--profile langfuse-local` until someone asks.
- **pgBouncer in session mode locally, transaction mode in prod.** Session mode avoids the asyncpg + pgBouncer prepared-statement gotcha for dev simplicity; prod (Session 1's stateful-core stack) uses transaction mode for real connection multiplexing.
- **Swarm `version: "3.9"`.** Highest Swarm-compatible Compose schema.
- **`external: true` everywhere for networks + secrets in swarm.yml.** Session 1's cluster bootstrap is responsible for creating them; deploy fails fast if they're missing.

**B7 compliance:** both files carry the file header (with ⭐ START HERE), section headers, role-not-syntax comments, RELATED FILES footer.

**Constraints honored:** C3, C7, C11, D1/D8, F9, F13, G2, I2.

**Next:** PR 3 (rebase-pending) — `project.config` + `shared-config.yaml` + `secrets.yaml.template` + `.env.example` on `session-2/template-skeleton-configs`.

---

## 2026-05-13T09:45:54Z — 6abba4d
### Action
Session 2 Day 1 PR 1: pyproject.toml + Dockerfile + .dockerignore

### Files touched
- yral-rishi-agent-new-service-template/.dockerignore
- yral-rishi-agent-new-service-template/Dockerfile
- yral-rishi-agent-new-service-template/pyproject.toml
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-2-LOG.md
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-2-STATE.md

### Notes
Auto-appended by post-tool-use.sh hook. Add manual milestone entries
above this line when crossing a meaningful boundary.

---

## 2026-05-13 — Day 1, first commit (PR 1: pyproject + Dockerfile + .dockerignore)

**Branch:** `session-2/template-skeleton-pyproject-and-dockerfile`

**Files added (3):**
- `yral-rishi-agent-new-service-template/pyproject.toml` — Python 3.12 pin, hatchling build backend, runtime deps (fastapi 0.115.12, uvicorn[standard] 0.34.0, asyncpg 0.30.0, redis 5.2.1, httpx 0.28.1, pydantic 2.10.5, alembic 1.14.0), dev extras (pytest 8.3.4 + pytest-asyncio 0.25.2). All deps pinned ==.
- `yral-rishi-agent-new-service-template/Dockerfile` — two-stage build: stage 1 installs deps into /opt/venv via hatchling; stage 2 copies venv + app code into a slim Python 3.12 image, runs as non-root `appuser` UID 1001, CMD `uvicorn app.main:app`.
- `yral-rishi-agent-new-service-template/.dockerignore` — filters .git, __pycache__, .venv, editor crud, local .env files, docs/tests, compose files. Deliberately does NOT exclude Dockerfile/.dockerignore themselves (some builders need them in context).

**Decisions made (worth recording):**
- Hatchling chosen as build backend (over setuptools / poetry-core) — modern PEP 621 default, no plugin baggage, doesn't lock us into a specific CLI.
- Multi-stage Dockerfile uses the simplest pattern: copy `pyproject.toml + app/` once, run `pip install .` once. We forgo the more-elaborate "install deps in a separate layer for cache efficiency" trick — that optimization can come later if build time becomes a real complaint (A2.1: simple > clever).
- Dev extras include only `pytest` + `pytest-asyncio` for Day 1. Coverage tooling + ruff + pytest.ini land in Day 3 with the CI workflows (matches J1 ramp-up).
- Sentry SDK + Langfuse client deferred to Day 2 (added when their middleware files land — keeps Day 1 PR scope tight to "deps explicitly listed in role spec").
- Dockerfile references `app/main.py` (added Day 2). PR 1 alone won't `docker build` successfully — that's expected; PR description notes it. No CI yet (Day 3).

**B7 compliance:** every file carries the file-header block + section headers + role-comments-not-syntax + RELATED FILES footer. Voice matches existing `yral-rishi-hetzner-infra-template` for continuity.

**Constraints honored:**
- F12: Python 3.12 + asyncpg uniformly.
- F2: zero touches to `yral-rishi-hetzner-infra-template` (read patterns only).
- A2.1: kept things boring + simple; no clever optimizations.
- B7: full doc structure on every file (including pyproject.toml).
- D1/D8: no secrets in committed files; `.env.local` is in `.dockerignore`.

**Next:** PR 2 — docker-compose.yml + docker-compose.swarm.yml on branch `session-2/template-skeleton-compose`.

---

(no entries before this — pre-launch stub by coordinator 2026-04-29)
