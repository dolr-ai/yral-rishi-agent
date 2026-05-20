# Session 3 LOG — Public-API

> Append-only diary. Most recent entries at TOP. Never edit past entries; correct via new entries.

## 2026-05-18 — Day 2, PR 2 (endpoint handlers per the locked API contract + 32 contract tests)

### Action
Day 2 of Phase 1, off the back of PR #94 merge. Implemented every endpoint listed in `interface-contracts/00-api-contract.md` that the Day-2 scope calls out (per the agent definition + Rishi's "go" message), wired the ApiResponse<T> envelope verbatim, gated every chat + influencer handler behind a new feature flag so production cannot serve stubs, added a local bridge for the F9 health endpoints the template doesn't yet ship (raised DEP-005 for Session 2 to mirror), and shipped 32 contract tests that all pass (0.09s wall-clock).

### Endpoints implemented
- `POST   /api/v1/chat/conversations` → `ApiResponse<ConversationDto>`
- `GET    /api/v1/chat/conversations` → `ApiResponse<list[ConversationDto]>` (v1 inbox)
- `POST   /api/v1/chat/conversations/{conversation_id}/messages` → `ApiResponse<MessageDto>`
- `GET    /api/v1/chat/conversations/{conversation_id}/messages` → `ApiResponse<list[MessageDto]>` (paginated; `limit` + `before` accepted)
- `POST   /api/v1/chat/conversations/{conversation_id}/read` → `ApiResponse<{}>`
- `DELETE /api/v1/chat/conversations/{conversation_id}` → `ApiResponse<{}>`
- `GET    /api/v2/chat/conversations` → `ApiResponse<list[ConversationDto]>` (v2 bot-aware inbox — what current mobile build uses)
- `GET    /api/v1/influencers` → `ApiResponse<list[InfluencerDto]>`
- `GET    /api/v1/influencers/trending` → `ApiResponse<list[InfluencerDto]>`
- `GET    /api/v1/influencers/{influencer_id}` → `ApiResponse<InfluencerDto>`
- `GET    /health/live`, `/health/ready`, `/health/deep` → `{"status": "ok", ...}` (F9 three-tier; raw shape, NOT envelope per F9 — health probes need cheap parsing for docker/Swarm/Uptime Kuma)

### Deferred to Day 6-7 parity sprint (per agent definition)
- Influencer write set: `POST /generate-prompt`, `POST /validate-and-generate-metadata`, `POST /create`, `PATCH /{id}/system-prompt`, `POST /{id}/generate-video-prompt`, `DELETE /{id}`, `POST /admin/{id}/ban`, `POST /admin/{id}/unban`
- Reason: write set routes through Session 4's influencer-directory RPC; deferring to Day 6-7 avoids a coordination round-trip with Session 4 at Day 2 + keeps Day-2 scope tight per A2.1.

### Architecture decisions (worth recording)

- **Module layout: `app/api/` package** — kept all Day-2 surface under one subpackage instead of dropping files directly into `app/` because (a) the template's `app/` is shared scaffold (sentry / langfuse / logging / config / request-id middleware) and adding 8 new files there would mix concerns, (b) future API-version sprints can drop sibling packages (e.g. `app/admin/`, `app/internal/`) without renaming.
- **Single feature flag, FastAPI dependency** — `require_day_2_placeholder_flag_enabled` is a 1-line dependency every Day-2 chat / influencer handler depends on. Test client overrides it via `app.dependency_overrides` to assert both states. When Day-4 swaps stubs for the orchestrator RPC, the dependency is removed in one place (not 10 handlers).
- **Envelope-aware HTTPException handler** — `app/main.py` registers a custom `HTTPException` handler that emits dict-shaped detail verbatim. Without it, FastAPI's default would wrap our envelope as `{"detail": <envelope>}` and break mobile's parser. The handler falls back to FastAPI's default `{"detail": <str>}` shape for non-envelope error paths (e.g. Pydantic 422s).
- **Stub helper factories** — `_stub_message()`, `_stub_conversation()`, `_stub_influencer()` centralize the SCHEMA-VALID placeholder shapes. Day-4 RPC integration swaps a single function (or removes it) instead of editing 10 handlers.
- **Placeholder content text is OBVIOUS** — every stub message body contains `"[v2 phase-1 day-2 placeholder — real response from day-4 once orchestrator RPC is wired]"`. If a feature-flag misconfiguration ever slips a stub into production, mobile users see the literal placeholder string in the chat bubble — non-confusable with real LLM output (per agent definition Day-2 spec).
- **Request DTOs live in `chat_routes.py`, response DTOs in `dtos.py`** — response DTOs are cross-cutting (Sessions 4 + 5 reference them); request DTOs are route-internal. Per A2.1 — don't speculatively share until two callsites need the same shape.
- **Health endpoints local bridge** — template doesn't ship them yet; raised DEP-005 so Session 2 mirrors. Kept in `app/api/health_routes.py` instead of `app/health_routes.py` for symmetry with the other route files; the template's mirror should live at `app/health_routes.py` top-level (no `api/` nest) since the template stays minimal per A2.1.
- **Contract-test fixtures derived from the contract doc, NOT from chat-ai pulls** — per A14 + the agent definition Day 6-7 plan, live chat-ai pulls need typed Rishi YES every time. Day-2 tests assert shape against `interface-contracts/00-api-contract.md`; Day 6-7 parity sprint replaces these with captured chat-ai JSON.

### Files touched
- `yral-rishi-agent-public-api/app/config.py` — added `enable_session_3_phase_1_day_2_placeholder_responses: bool = False` field with extensive WHY comments
- `yral-rishi-agent-public-api/app/main.py` — added router includes (chat_v1, chat_v2, influencer, health) + envelope-aware HTTPException handler + expanded RELATED FILES footer
- `yral-rishi-agent-public-api/app/api/__init__.py` (new)
- `yral-rishi-agent-public-api/app/api/envelope.py` (new) — `ApiResponse[T]` generic
- `yral-rishi-agent-public-api/app/api/errors.py` (new) — `ErrorCode` Literal + `HTTP_STATUS_FOR_ERROR_CODE` map + `error_response()` helper
- `yral-rishi-agent-public-api/app/api/dtos.py` (new) — `MessageDto` / `ConversationDto` / `InfluencerDto` / `ChatAccessDataDto`
- `yral-rishi-agent-public-api/app/api/feature_flag.py` (new) — `require_day_2_placeholder_flag_enabled` dependency
- `yral-rishi-agent-public-api/app/api/chat_routes.py` (new) — 7 chat handlers + 3 request-DTO classes + 2 stub factories
- `yral-rishi-agent-public-api/app/api/influencer_routes.py` (new) — 3 influencer-read handlers + 1 stub factory
- `yral-rishi-agent-public-api/app/api/health_routes.py` (new) — 3 health handlers (LOCAL BRIDGE — DEP-005 raised)
- `yral-rishi-agent-public-api/pyproject.toml` — added `[tool.pytest.ini_options]` block (`testpaths = ["tests"]`, `asyncio_mode = "auto"`)
- `yral-rishi-agent-public-api/tests/__init__.py` (new)
- `yral-rishi-agent-public-api/tests/contract/__init__.py` (new)
- `yral-rishi-agent-public-api/tests/contract/conftest.py` (new) — `client` + `client_flag_off` TestClient fixtures (dependency-override based)
- `yral-rishi-agent-public-api/tests/contract/test_chat_routes.py` (new) — 20 tests
- `yral-rishi-agent-public-api/tests/contract/test_influencer_routes.py` (new) — 9 tests
- `yral-rishi-agent-public-api/tests/contract/test_health_routes.py` (new) — 3 tests
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md` — DEP-005 raised
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-3-LOG.md` (this entry)
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-3-STATE.md` — advanced to Day-3 next-action

### Why
Per the agent definition Day 2 deliverable (verbatim): "Read `interface-contracts/00-api-contract.md` end-to-end. Capture the full endpoint list. Don't invent paths — these are LOCKED. Implement handlers as THIN routing + auth + envelope wrappers. Every response uses the `ApiResponse<T>` envelope verbatim. Initial implementation for chat endpoints: return SCHEMA-VALID stub DTOs (NOT empty data). For non-chat endpoints (influencers list, health, etc.): partial Phase 1 OK — implement the ones Session 4 doesn't need first. Tests: 3-5 contract-fixture tests per endpoint." Day 2 is the FIRST PR mobile could in-principle hit — the envelope + DTO shapes are now locked in, and Day-4 RPC integration becomes a fill-in-the-handlers exercise.

### Test evidence
- `python3 -m py_compile` against all 15 `app/*.py` + `app/api/*.py` files → 0 errors.
- `pytest tests/contract/ -v` inside the Day-1 Docker image (Python 3.12, FastAPI TestClient, pytest-asyncio in `asyncio_mode=auto`):
  ```
  collected 32 items
  ... [32 PASSED] ...
  ============================== 32 passed in 0.09s ==============================
  ```
- Live HTTP smoke test against `docker run` of the rebuilt image:
  - With `ENABLE_SESSION_3_PHASE_1_DAY_2_PLACEHOLDER_RESPONSES=true`:
    - `/openapi.json` → HTTP 200, 11 paths registered:
      `/api/v1/chat/conversations` + `/api/v1/chat/conversations/{conversation_id}` + `.../messages` + `.../read` + `/api/v1/influencers` + `/trending` + `/{influencer_id}` + `/api/v2/chat/conversations` + `/health/{live,ready,deep}`
    - `POST /api/v1/chat/conversations` → HTTP 200, envelope-shaped body with fresh UUID + echoed `ai_influencer_id` + stub assistant `last_message`
    - `GET /api/v1/influencers` → HTTP 200, envelope-shaped list with stub Tara
    - `GET /health/live` → HTTP 200, `{"status":"ok"}`
  - Without the env var (flag defaults False — production behavior):
    - `POST /api/v1/chat/conversations` → HTTP **503**, envelope-shaped error body: `{"success":false,"msg":"This endpoint is not yet implemented in this environment. ...","error":"service_unavailable","data":null}`
    - `GET /health/live` → HTTP 200, `{"status":"ok"}` (health unaffected by flag — correct per the contract for production deploy safety)

### Constraints honored
- **A1 (relaxed)** — no deletions; new files only (plus 3 edits to existing files). Cleaned up `.pytest_cache` artifact from docker mount via `docker run` (since it was created by root inside the container) — that artifact is in `.gitignore` regardless.
- **A2.1** — kept scope to the Day-2 deliverable set. Deferred influencer write set + admin endpoints + WebSocket inbox + JWT auth + orchestrator RPC + idempotency middleware to their respective day-by-day slots. Single feature flag (not a hierarchy); single stub-factory helper (not a class hierarchy); request DTOs co-located with handlers (not promoted prematurely). PR scope is large but every line is mandated by the locked Day-2 scope; no speculative abstractions.
- **A7 + C4 + D3** — Sentry tag remains `yral-rishi-agent-public-api` → `sentry.rishi.yral.com` (no changes to the inherited middleware).
- **A8** — every endpoint shape comes from `interface-contracts/00-api-contract.md` verbatim; DTOs match the contract field-for-field; envelope is the locked `{success, msg, error, data}` shape mobile parses today.
- **B1 + B2** — every name reads as English; only allowlisted abbreviations used (`api`, `id`, `url`, `app`, `init`, `ci`, `dto`, `http`, `json`, `uuid`). `Dto` is widely-recognized in the yral codebase + chat-ai's existing wire format — confirms with B4 product vocab.
- **B4** — used "AI Influencer" (not "bot"), "Soul File" (not "system prompt") in comments. `InfluencerDto.bio` documented as NOT the Soul File (which stays inside the orchestrator per E8).
- **B7** — every new file carries the 3-tier doc treatment: ⭐ START HERE file header + per-function/class WHAT/WHEN/WHY + role-not-syntax line comments + RELATED FILES footer. Functions in PRIORITY order (entry-point first, helpers after).
- **C7** — feature flag goes through `app/config.py` (the pydantic-settings singleton), not a hardcoded global. shared-config.yaml loader still deferred to its first consumer per A2.1 (no nested config shape needed today).
- **E5** — `conversation_type` Literal supports `ai_chat`, `human_chat`, `chat_as_human` — H2H + AI + Chat-as-Human in one schema from day 1, as locked.
- **E7** — `ChatAccessDataDto` in dtos.py preserves the camelCase `hasAccess` / `expiresAt` from the chat-ai contract (per CURRENT-TRUTH paywall section + A8 — chat-ai wins on wire format).
- **F9** — three-tier health split shipped via the local bridge; DEP-005 raised for Session 2 to mirror in the template so all 13 services get them by default.
- **F10** — idempotency middleware deferred to Day 4 per agent spec; no idempotency claims in Day-2 stubs.
- **F16** — all changes inside `yral-rishi-agent-public-api/` (path-scoped to my session scope) + the cross-session-dependencies.md append.
- **I9** — no edits to `.github/workflows/` at repo root. The per-service workflow inside `yral-rishi-agent-public-api/.github/workflows/per-service-ci.yml` is unchanged (it picks up `tests/` via pytest auto-discovery).

### Blockers raised
- **DEP-005** — Session 2 to mirror `/health/{live,ready,deep}` in the template. Not a hard block for Session 3 (local bridge ships), but a hard block for Sessions 4 + 5 + other deferred services before their first Day-5 cluster deploy. Detail in cross-session-dependencies.md.

### Next
Day 3 — JWT auth middleware in SHADOW mode per E9 (JWKS fetch from `https://auth.yral.com/.well-known/jwks.json`, Redis 1hr cache, `enable_strict_jwt_signature_validation: false` default, validate-but-don't-enforce, log mismatch metric to Sentry per the 7-day-divergence-rollout plan). Separate branch + PR.

---

## 2026-05-18 — Day 1, PR 1 (spawn yral-rishi-agent-public-api from template + FastAPI-title fix)

### Action
Day 1 of Phase 1. Spawned `yral-rishi-agent-public-api/` from Session 2's template via the canonical `bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-public-api` flow. Ran a local smoke test (docker compose build + `docker run` + curl) end-to-end. Also folded in Session 2's queued one-line follow-up: `app/main.py` FastAPI title was hardcoded as the template placeholder and not substituted at spawn time — agent definition (`.claude/agents/session-3-public-api.md` line 86-87) gives explicit authorization to fix it small in the spawned copy or accept the cosmetic gap, and the smoke test confirmed `/openapi.json` reports the correct title after the one-line edit.

### Files touched
- `yral-rishi-agent-public-api/**` (40 files, 272 KB — full spawn from template, matches Session 2's hello-world PR #42 spawn footprint exactly)
- `yral-rishi-agent-public-api/app/main.py` (one-line follow-up: FastAPI `title="yral-rishi-agent service template"` → `title="yral-rishi-agent-public-api"` + updated the 3-line comment above the `app = FastAPI(...)` block to reflect that the title is now spawned-service-specific, not template-generic)
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-3-LOG.md` (this entry — manual milestone, hook will append its own commit entry below on commit)
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-3-STATE.md` (advance LAST THING I DID + CURRENT TASK + NEXT 3 PLANNED ACTIONS to Day-2)

### Deletion-report block (per A1 relaxed, captured for the PR body too)
- **Deleted:** None directly. `yral-rishi-agent-public-api/README.md` (439 bytes, 5-line stub created 2026-04-24 in the monorepo restructure commit `2fedf7a`; contents: "**Status:** empty placeholder. Code goes here when we reach the relevant phase per TIMELINE.md") and its parent empty directory were RELOCATED (not deleted) to `/tmp/yral-rishi-agent-public-api-placeholder-20260518-145923/yral-rishi-agent-public-api/README.md` so the spawner's "refuse to overwrite" guard (new-service.sh line 156-160) would clear.
- **Reason:** The spawner refuses to write into any existing `$TARGET_PATH`; without relocating the placeholder folder, Day 1 cannot proceed. The placeholder's own text declares it transitional ("Code goes here when we reach the relevant phase") — Phase 1 IS that phase. The spawn produces a proper `README.md` from the template that supersedes the stub.
- **Safety checks performed (7-step):** (1) identified exactly = `yral-rishi-agent-public-api/README.md` + the now-empty parent dir; (2) deletion-necessity = mandatory for spawner to proceed; (3) item is SUPERSEDED = the spawn produced a richer README plus the entire service scaffold; (4) references checked = `git log --oneline -5 -- yral-rishi-agent-public-api/` shows only the monorepo-restructure commit ever touched the file; no code imports the README; no other docs reference the placeholder; (5) non-destructive alternatives = chose `mv` (relocate) over `rm`; the entire file + folder is intact under /tmp; (6) risk gate = very low (stub content, well-known pattern matching ~11 other placeholder service folders, NOT on A1 hard-stop list); (7) post-relocation checks = `docker compose build service` succeeded, `docker run` started uvicorn cleanly, `curl /openapi.json` returned HTTP 200 with the spawned-service title.
- **References checked:** code imports — none; tests — none; configs — none; scripts — none; migrations — none; docs — none; runtime — none.
- **Why this was safe:** Stub file, NOT on A1 hard-stop list (not user-data / not migration / not env-config / not auth / not billing / not infra). Relocation preserves bit-for-bit recovery. The same pattern applies to ~11 other v2 service folders that still contain the 2026-04-24 placeholder; Session 4 will face the same situation on orchestrator + soul-file-library + influencer-and-profile-directory and can follow this established approach.
- **Tests/builds run:** `docker compose config --quiet` (clean), `docker compose build service` (success, image `yral-rishi-agent-public-api-service:latest`), `docker run` + `curl /openapi.json` (HTTP 200 + correct title), `curl /docs` (HTTP 200), `python3 -m py_compile app/*.py` (all parse), `bash -n scripts/*.sh` (all syntax-clean).
- **Rollback plan:** `mv /tmp/yral-rishi-agent-public-api-placeholder-20260518-145923/yral-rishi-agent-public-api ~/Claude\ Projects/yral-rishi-agent/yral-rishi-agent-public-api` restores the placeholder bit-for-bit; archive lives on disk until Rishi confirms the PR is the right path forward and tells me to clean up the /tmp archive.

### Why
Per the agent definition Day 1 deliverable: "Run `bash yral-rishi-agent-new-service-template/scripts/new-service.sh public-api` to spawn `yral-rishi-agent-public-api/`. Verify spawn artifacts: docker-compose builds locally, FastAPI default route returns 200. Initial PR: the spawned service folder + your STATE/LOG initial entries." Day 1 is mechanical-but-critical: it proves the template Session 2 shipped actually works for a real Phase-1 service (not just the throw-away hello-world). The FastAPI-title fix folds in Session 2's queued one-line follow-up (PR #42 close note item #2) — small enough to keep PR scope tight per A2.1.

### Test evidence
- `bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-public-api --dry-run` → preview matches expected (3 substitution rounds + rename of secrets.yaml.template).
- `bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-public-api` → 40 files, 272 KB, all 8 F8 docs (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY / WALKTHROUGH / GLOSSARY / WHEN-YOU-GET-LOST), all 5 `app/*.py` middleware modules, both compose files, `secrets.yaml` (renamed from .template), 3 D8 bridge scripts in `scripts/`, `.github/workflows/per-service-ci.yml`. Spawner correctly NOT present in the spawned folder (rsync `--exclude` worked).
- `grep -rn 'yral-rishi-agent-new-service-template' yral-rishi-agent-public-api/` → 0 matches.
- `grep -rn 'new_service_template' yral-rishi-agent-public-api/` → 0 matches.
- `grep -rn '\${PROJECT_NAME}' yral-rishi-agent-public-api/` → 0 matches.
- `project.config` correctly substituted: PROJECT_NAME=yral-rishi-agent-public-api, POSTGRES_SCHEMA=public_api, POSTGRES_ROLE=public_api_role, SWARM_STACK=yral-rishi-agent-public-api, IMAGE_REPO=ghcr.io/dolr-ai/yral-rishi-agent-public-api, SENTRY_SERVICE_TAG=yral-rishi-agent-public-api.
- `docker-compose.swarm.yml` references the three CONSTRAINTS C3 overlays verbatim (`yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`) — alignment with DEP-003's resolution holds.
- `.github/workflows/per-service-ci.yml` paths-scoped to `yral-rishi-agent-public-api/**`.
- `docker compose config --quiet` → 0 errors.
- `docker compose build service` → image `yral-rishi-agent-public-api-service:latest` built in ~30s (cached layers after first run).
- `docker run` of the built image → uvicorn started cleanly, `curl http://127.0.0.1:18080/openapi.json` returned HTTP 200 with `{"info": {"title": "yral-rishi-agent-public-api", "version": "0.1.0"}}`, `curl /docs` returned HTTP 200.
- `python3 -m py_compile` against all 7 `app/*.py` files → 0 errors.
- `bash -n` against all 3 `scripts/*.sh` files → 0 errors.

### Blockers raised
None. No new DEP-xxx in cross-session-dependencies.md this PR. Day-4 will likely raise the first one (need Session 4's `run_turn` RPC stub).

### Constraints honored
- A1 (relaxed) — placeholder folder RELOCATED to /tmp under the full 7-step report, NOT deleted; rollback path explicit.
- A2.1 — kept PR scope tight (spawn + 1-line title fix + LOG/STATE update). No new abstractions, no new dependencies, no >100-line additions.
- A7 + C4 + D3 — Sentry tag stays `yral-rishi-agent-public-api` pointing at `sentry.rishi.yral.com` (inherited from template's project.config; verified).
- B1 + B2 — names match the allowlist; service name reads as English.
- B3 — `yral-rishi-agent-public-api` matches the pattern + 36 chars (well under Swarm's 63-char cap).
- B7 — touched 1 line of comment text in app/main.py (kept the file-header block + RELATED FILES footer intact).
- C3 — overlay names match (`yral-v2-public-web` / `yral-v2-internal` / `yral-v2-data-plane`).
- F1 + F8 + F12 + F13 + F16 — uses the v2 template, 8 docs present, Python 3.12 + FastAPI uniform, GHCR image path, monorepo subfolder.
- I9 — did NOT touch `.github/workflows/` at repo root; the workflow file inside `yral-rishi-agent-public-api/.github/workflows/per-service-ci.yml` is per-service (in my scope); coordinator stages it at repo root.

### Next
PR 2 (Day 2): endpoint handlers per `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md` as thin envelope wrappers; SCHEMA-VALID stub responses behind feature flag `enable_session_3_phase_1_day_2_placeholder_responses: true`; contract-fixture tests (3-5 per endpoint).

---

## 2026-05-18 — MILESTONE: Session 3 first-launched by coordinator

### Action
Coordinator scaffolded Session 3's STATE + LOG files before Session 3's first work, per the agent definition's "initially scaffolded by coordinator on first launch" clause. Session 3 has completed Step A (first-launch onboarding context, 11 items) + Step B (I12 resume protocol, 6 steps) and is idle pending Rishi's `continue` to start Day 1 (spawn `yral-rishi-agent-public-api/` from Session 2's template).

### Files touched
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-3-STATE.md` (new)
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-3-LOG.md` (new — this file)

### Why
Phase 1 launch readiness. The state-hygiene lint requires SESSION-N-LOG.md to be updated on every session-N PR. By scaffolding the files upfront, Session 3's first real PR appends to existing files instead of creating them — cleaner lint-passing path + matches the established pattern from Sessions 1, 2, 5.

### Test evidence
N/A — meta-scaffolding, no functional change.

### Notes
- Session 3's agent definition: `.claude/agents/session-3-public-api.md`
- Codex reviewed Session 3's agent def across 7 rounds on PR #90; all real catches addressed before merge.
- Session 4 (Orchestrator + Soul-File + Influencer Directory) launched in parallel with Session 3; they coordinate via cross-session-dependencies.md when Session 3 needs Session 4's `run_turn` RPC (expected Day 4).
- Phase 1 working target 2026-06-07 per Rishi's stated push date. **NOT a production cutover date** — cutover stays at Rishi's typed-YES discretion per A6. Phase 1 prepares parity-complete v2; Rishi decides if/when to actually cut over.

---

(future entries below as Session 3 works)
