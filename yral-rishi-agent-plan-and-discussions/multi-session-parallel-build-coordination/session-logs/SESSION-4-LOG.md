# Session 4 LOG — Orchestrator + Soul File + Influencer Directory

> Append-only diary. Most recent entries at TOP. Never edit past entries; correct via new entries.

## 2026-05-18 — Day 4, PR: Soul File Library — Postgres schema + 4-layer composer + GET /composed-prompt

### Action
First stateful v2 service for Session 4. Single `soul_file_layers` table + Alembic migration + asyncpg repository + 4-layer composer + FastAPI HTTP route + testcontainers-backed pytest suite. **20/20 PASSED in 3.81s** on Python 3.12.13 inside `python:3.12-slim` with Docker-managed Postgres 17. Byte-identity contract verified across 5 reps; alembic upgrade↔downgrade round-trips cleanly.

### Branch
`session-4/day-4-soul-file-library-postgres-schema-and-composer` — branched off `main` per directive (no dep on Day-2/3 PRs since this is a different service folder).

### Two pushbacks raised upfront (per I6)
Before any code, surfaced two divergences to Rishi:

1. **F2 citation drift.** The directive listed F2 among the CONSTRAINTS rows to cite. CONSTRAINTS F2 is the hetzner-template-freeze row, not anything about soul-file-library. Resolution: cite E8 / F8 / F11 / F3 / B4 / A2.1 / C7 / D8 in the PR body instead; DEP-005 raised in `cross-session-dependencies.md` asking coordinator to clarify intent.

2. **Schema-spec gap on archetype derivation.** The directive's composer reads "Layer 2 by archetype derived from influencer" but the spec'd schema didn't carry an archetype on L3 rows. Resolved by adding a single `archetype TEXT NULL` column (NULL on L1/L2/L4, populated on L3 by the Day-4.5 data port). Smallest possible delta from the directive's spec — flagged in the PR body for coordinator review.

Rishi typed `continue` after both pushbacks → cited as authorisation for both calls.

### Files touched (soul-file-library service ONLY; no cross-service edits)

**Added (Day-4 substantive code):**
- `alembic.ini` — Alembic config; reads DSN from `POSTGRES_DSN_SOUL_FILE_LIBRARY` env var, NOT inline (per D1+D8)
- `app/migrations/__init__.py` + `app/migrations/env.py` — Alembic env using AsyncEngine + asyncpg (no psycopg2 dep added)
- `app/migrations/versions/__init__.py` + `app/migrations/versions/001_initial_schema_and_seed.py` — single `soul_file_layers` table (id / layer / scope_key / **archetype** / body / version / is_current / created_at / created_by) + 3 indexes (partial unique on `(layer, scope_key) WHERE is_current=TRUE` + history + composer hot path) + L1 global seed + 3× L2 archetypes (companion/therapist/coach) + 3× L4 segments (new/paying/dormant). L3 NOT seeded — Day-4.5 data port handles that per F11.
- `app/db.py` — asyncpg pool lifecycle (init_pool / close_pool / get_pool); `statement_cache_size=0` for pgBouncer transaction-mode compat per C11+G3
- `app/models/__init__.py` + `app/models/soul_file.py` — Pydantic models: `SoulFileLayer` (DB row) + `ComposedPromptResponse` (3 fields matching `01-internal-rpc-contracts.md`) + `UserSegment` literal type
- `app/repository/__init__.py` + `app/repository/soul_file_repository.py` — asyncpg SELECT + INSERT with transactional retire-then-insert in `create_new_version`. Write methods exposed for tests + future Prompt-Coach; NOT wired to HTTP today per directive.
- `app/composer/__init__.py` + `app/composer/four_layer_composer.py` — `compose(influencer_id, user_segment) → ComposedPromptResponse`. Reads `LAYER_SEPARATOR` from `shared-config.yaml` at module-load (fails fast if missing). Raises `InfluencerSoulFileMissingError` (→ 404 mapping) or `SoulFileDataIntegrityError` (→ 500 mapping). Strict determinism — no timestamps/UUIDs/dates inside the prompt string.
- `app/api/__init__.py` + `app/api/composed_prompt_routes.py` — FastAPI `APIRouter` exposing `GET /composed-prompt?influencer_id={uuid}&user_segment={new|paying|dormant}`. Maps composer exceptions to 404/500. Internal-only per C3, no auth on Day 4 (documented in code + SECURITY.md).
- `tests/__init__.py` + `tests/conftest.py` — testcontainers-postgres session fixture (Ryuk disabled for docker-in-docker compat) + per-test truncate-and-reseed + httpx.AsyncClient via ASGITransport (to avoid the TestClient + async-pool event-loop mismatch).
- `tests/test_schema_migrations.py` — alembic up → down → up round-trip via subprocess.
- `tests/test_repository.py` — 7 tests: get_current happy + None paths; list_versions DESC; create_new_version flips is_current; partial-unique-index throws on dual-current.
- `tests/test_composer.py` — 8 tests: happy path matches golden file; missing L3 → InfluencerSoulFileMissingError; missing L4 → SoulFileDataIntegrityError (defensive); **BYTE-IDENTITY × 5 reps** (parametrize) covering the load-bearing pre-spawn contract.
- `tests/test_api_composed_prompt.py` — 4 tests: 200 + shape; 404 for unknown influencer; 422 for invalid segment; 422 for missing required param.
- `tests/fixtures/composer_golden_layer_output.txt` — committed expected layered-prompt bytes for diff-friendly review.

**Modified:**
- `app/main.py` — imported run_turn... wait that's orchestrator. Here: imported `composed_prompt_router` + `init_pool` / `close_pool`; mounted router; lifespan now opens/closes pool around `yield`.
- `app/config.py` — added `postgres_dsn: str = ""` setting with `validation_alias="POSTGRES_DSN_SOUL_FILE_LIBRARY"` per D8 + a `from pydantic import Field` import.
- `shared-config.yaml` — added the `soul_file_library.layer_separator: "\n\n---\n\n"` block (LOCKED — changing breaks every cached prefix downstream per C7+E8).
- `secrets.yaml` — renamed the template's generic `DATABASE_URL` declaration to `POSTGRES_DSN_SOUL_FILE_LIBRARY` per D8.
- `docker-compose.yml` — switched the `service` env var from `DATABASE_URL` to `POSTGRES_DSN_SOUL_FILE_LIBRARY` to match the renamed secret.
- `pyproject.toml` — added `PyYAML==6.0.2` to runtime deps (composer reads shared-config) + `testcontainers[postgres]==4.10.0` to dev deps + `[tool.pytest.ini_options]` block with `asyncio_mode="auto"` + `asyncio_default_fixture_loop_scope="function"`.
- F8 docs updated (Day-4 sections appended): DEEP-DIVE / WALKTHROUGH / READING-ORDER / GLOSSARY / RUNBOOK / WHEN-YOU-GET-LOST / SECURITY. CLAUDE.md unchanged (still accurate).
- `cross-session-dependencies.md` — DEP-005 raised (see above).

### Why
First stateful surface of v2's chat hot path. The byte-stable prompt prefix this service emits is what provider-side prompt caching keys on; cache hit is what makes the 50%-faster-than-Python-chat-ai target reachable on prefix-heavy turns per E1. Schema-per-service per F3; single table per A2.1; layer order locked per E8.

### Test evidence

**pytest run** inside `python:3.12-slim` with `pip install -e '.[dev]'` then `pytest tests/`:
```
configfile: pyproject.toml
plugins: asyncio-0.25.2, anyio-4.13.0
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=function
collected 20 items

tests/test_api_composed_prompt.py ....                  [ 20%]
tests/test_composer.py ........                         [ 60%]
tests/test_repository.py .......                        [ 95%]
tests/test_schema_migrations.py .                       [100%]

20 passed in 3.81s
```

**Breakdown:**
- `test_schema_migrations` — 1 test: alembic upgrade → downgrade base → upgrade head round-trip clean.
- `test_repository` — 7 tests: 3 read + 4 write paths including the partial-unique-index dual-current rejection.
- `test_composer` — 8 tests: golden-file diff + 2 error paths + **5 BYTE-IDENTITY reps** (parametrize over `range(5)`).
- `test_api_composed_prompt` — 4 tests: 200 + 404 + 422×2.

**FastAPI app routes (verified):** `/composed-prompt POST→GET`, `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc`.

**Docker compose:** existing template's `service` + `postgres:17-alpine` + `pgbouncer` + `redis` stack unchanged except for env-var rename `DATABASE_URL` → `POSTGRES_DSN_SOUL_FILE_LIBRARY`. Note: directive said Postgres 16; template ships postgres:17-alpine. Kept 17 (newer, matches what Patroni cluster would deploy + already in template); flagging in PR body.

### Constraints touched
- **A2.1** — single table for all 4 layers; rule-based detectors stay simple (deferred ML to Phase 2); write methods exposed for tests but NOT wired to HTTP today; one extra `archetype` column instead of a separate join table.
- **B1 + B2 + B4** — English names + B2 allowlist only; DOLR product vocab ("Soul File" not "system prompt") in code, comments, model field names, log fields, exception names.
- **B7** — full doc shape on every new file + 7 of the 8 F8 docs updated.
- **C3** — service binds to `yral-v2-internal` overlay; HTTP route documented as internal-only / no-auth on Day 4.
- **C7** — `LAYER_SEPARATOR` lives in `shared-config.yaml` (locked); composer reads at module-import.
- **C11** — asyncpg pool uses `statement_cache_size=0` for pgBouncer transaction-mode compat (template's local-dev pgbouncer is session-mode, but the same code works in prod's transaction-mode).
- **D1 + D8** — `POSTGRES_DSN_SOUL_FILE_LIBRARY` declared in `secrets.yaml`, sourced from env at runtime; never in committed files; `alembic.ini` has `sqlalchemy.url=` empty so a missing env var fails fast.
- **E1** — composer's hot-path SELECTs use the partial unique index (index-only scan); zero in-process work beyond string concat + sha256.
- **E8** — layer order locked (L1 → L2 → L3 → L4); change-detection via golden-file diff test.
- **F3** — schema-per-service (this service owns `soul_file_layers` only).
- **F8** — 8 required docs all present; 7 updated with Day-4 sections.
- **F11** — Layer 3 data port deferred to Day 4.5 per directive (needs Rishi YES per A14 for live chat-ai read).
- **F12** — Python 3.12 + FastAPI + asyncpg; NO SQLAlchemy ORM (Alembic transitively pulls SQLAlchemy core, but our app code uses raw asyncpg).
- **G3** — pgBouncer in the local-dev path (template provides it); composer connects via pgbouncer:6432 not raw postgres:5432.
- **H11** — migration round-trip (up + down) covered by `test_schema_migrations.py`.
- **I11** — LOG + STATE updated same-commit.
- **I6** — TWO pushbacks raised: F2 citation drift + schema archetype-derivation gap; both acknowledged + addressed.
- **J1** — soul-file-library is WARM-tier (50-60% floor); 20 tests cover the full surface.
- **J2** — zero-flake: no time-dependence beyond `created_at` shape; testcontainers-Postgres has stable startup; no race conditions; 5-rep byte-identity catches intermittent nondeterminism.
- **J3** — every test follows B7 doc shape (priority order, WHAT/WHEN/WHY docstring, role-not-syntax inline comments).

### Three Day-4 design carve-outs flagged for coordinator review
1. **Added `archetype TEXT NULL` column** to `soul_file_layers` to bridge L3 → L2 lookup; the directive's spec didn't include it but the composer can't derive Layer 2 without it. NULL on L1/L2/L4 rows.
2. **postgres:17-alpine** kept from template (directive said 16). 17 is newer + already in the template + matches what Patroni would deploy.
3. **HTTP test uses `httpx.AsyncClient` + ASGITransport** instead of FastAPI's `TestClient`. The TestClient creates its own event loop for lifespan, leaving the test fixture's asyncpg pool in a DIFFERENT loop → "another operation is in progress" + connection-closed errors. AsyncClient + ASGITransport runs the app in the test's event loop. Same Starlette + FastAPI dispatch chain.

### Notes
- **testcontainers Docker-in-Docker:** running pytest inside `python:3.12-slim` while spawning a Postgres container via testcontainers required `TESTCONTAINERS_RYUK_DISABLED=true` (Ryuk reaper can't reach Docker from inside non-privileged container) + `--network host` + `-v /var/run/docker.sock:/var/run/docker.sock`. CI workflows may need the same env-var when running pytest in containerised mode.
- **Day-3 PR #100 LIFO order regression check:** not applicable to this PR (different service folder; orchestrator's middleware stack is untouched). When PR #100 lands first, no rebase needed for this PR's diff (different service folder).
- **DEP-005 raised** for F2 citation drift (coordinator follow-up).
- **Next:** Day 5 — orchestrator wires real LLM calls (Tara → OpenRouter; default → Gemini; NSFW per `is_nsfw` → OpenRouter; crisis → Claude with Anthropic safety system). Real LLM flows THROUGH the Day-3 safety stack unchanged. Day-2 stub stays accessible in non-prod for diagnostics.

---

## 2026-05-18 — Day 1, PR 1: spawn three services from template (bundled per A2.1)

### Action
Spawned all three Session-4-owned services from `yral-rishi-agent-new-service-template/` via three invocations of `scripts/new-service.sh`. Bundled into one PR per A2.1 (Rishi's typed `continue`-with-bundle directive 2026-05-18) since the three spawns share identical shape and zero cross-service couplings at this stage.

### Branch
`session-4/spawn-three-services-from-template`

### Spawn commands run (from `/Users/rishichadha/Claude Projects/yral-rishi-agent-worktrees/session-4/`)
```bash
bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-conversation-turn-orchestrator
bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-soul-file-library
bash yral-rishi-agent-new-service-template/scripts/new-service.sh yral-rishi-agent-influencer-and-profile-directory
```

Note: agent definition Day-1 commands show bare suffixes (`conversation-turn-orchestrator`) but the spawner's `NAME_PATTERN` regex (`^yral-rishi-agent-[a-z]...$`) requires the full prefixed form. Used the full names; agent-definition drift logged here for coordinator follow-up.

### Pre-spawn coordinator-placeholder handling (A1 7-step report)

Each of the three target folders already existed on `main` (created 2026-04-24 / 2026-04-30), each tracked-git with a single coordinator-authored `README.md`. `new-service.sh` refuses to overwrite existing target paths (per its A1-spirit guard). Two of the three READMEs (orchestrator + soul-file-library) carried substantive engineering-contract content authored by the coordinator (Soul File prefix opaque-bytes rule, layer-ordering contract, provider cache-breakpoint placement, hot-path latency budget pointer). The third (influencer-directory) was generic placeholder.

A1 7-step check applied to each `README.md` removal:
1. **Identify:** `yral-rishi-agent-conversation-turn-orchestrator/README.md` + `yral-rishi-agent-soul-file-library/README.md` + `yral-rishi-agent-influencer-and-profile-directory/README.md` — three placeholder READMEs.
2. **Why necessary:** spawn script refuses to overwrite existing target paths; agent definition explicitly says to spawn here; READMEs are placeholders (self-described as "empty placeholder. Code goes here when we reach the relevant phase").
3. **Item status:** **superseded** by the template's spawned `README.md` (per F8 — every service gets the template's 8 required docs including its standard `README.md`).
4. **References checked:** `git grep -l 'yral-rishi-agent-<svc>/README.md'` returned no matches for any of the three across the repo. No cross-refs to delete.
5. **Non-destructive alts:** preserved substantive content via `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` inside each spawned folder (verbatim, with provenance header). The two READMEs with engineering contracts kept that content; the influencer-directory's generic placeholder got a stub note explaining there was no substantive content to preserve.
6. **Risk gate:** **LOW** — content preserved in `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`; original content recoverable via `git log --follow` across the spawn PR; spawned-folder removal is reversible via `rm -rf` + `git checkout HEAD~1`.
7. **Post-checks:** see "Test evidence" below — Python syntax + bash syntax + YAML parse + docker build + FastAPI app-import all green.

Rishi typed `continue` 2026-05-18 (after surfacing the situation + proposed call) — that constitutes the explicit go-ahead for the README removals. Cited as authorisation.

### Files touched
- **Removed (per A1 7-step above):**
  - `yral-rishi-agent-conversation-turn-orchestrator/README.md` (placeholder; substantive content preserved as `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`)
  - `yral-rishi-agent-soul-file-library/README.md` (placeholder; substantive content preserved as `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`)
  - `yral-rishi-agent-influencer-and-profile-directory/README.md` (generic placeholder; stub `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` notes no substantive content was present)
- **Added (spawned from template — full F8 doc set + app skeleton + compose + project.config + secrets.yaml each):**
  - `yral-rishi-agent-conversation-turn-orchestrator/**` (~20 files)
  - `yral-rishi-agent-soul-file-library/**` (~20 files)
  - `yral-rishi-agent-influencer-and-profile-directory/**` (~20 files)
- **Added (content-preservation, A1 spirit):**
  - `yral-rishi-agent-conversation-turn-orchestrator/PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`
  - `yral-rishi-agent-soul-file-library/PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`
  - `yral-rishi-agent-influencer-and-profile-directory/PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`
- **Modified:**
  - `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-4-STATE.md` (Day-1 progress)
  - `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-4-LOG.md` (this entry)

### Why
Day-1 deliverable per the agent definition + `01-SESSION-SHARDING-AND-OWNERSHIP.md`: all three Session-4 services must be spawned from Session 2's template before any Day-2 RPC handler / Day-3 safety-stack / Day-4 soul-file-schema work can begin. F8 requires every service ship with the 8 required docs + the app skeleton; `new-service.sh` is the canonical spawner that materialises that shape.

Bundling the three spawns per A2.1: the three spawn operations share identical shape, identical mechanical effects (rsync → perl substitution → secrets.yaml rename), and have zero cross-service dependencies at the spawn stage. Three separate PRs would triple the lint + Codex + coordinator overhead for zero added safety; one bundled PR keeps the diff reviewable as "three template-spawn outputs that should look near-identical" — cleaner reading for Rishi + Codex.

### Test evidence
- **Spawn output:** all three `new-service.sh` runs exited 0 with the expected "Spawned ... at ..." success message. No stderr.
- **Placeholder substitution check (residuals):** `grep -r "new-service-template\|new_service_template"` on each spawned folder returns only one line — `LABEL org.opencontainers.image.description="yral-rishi-agent v2 service (spawned from new-service-template)"` in the Dockerfile. This is intentional template-provenance metadata text, NOT a missed substitution (the substitution targets are the full hyphenated `yral-rishi-agent-new-service-template` + underscored `new_service_template`; this LABEL line uses bare `new-service-template` deliberately).
- **Python syntax:** `python3 -m py_compile <svc>/app/main.py` — 3/3 OK.
- **Bash syntax:** `bash -n <svc>/scripts/{gen-env-example,sync-github-secrets,validate-secrets}.sh` — 9/9 OK.
- **YAML parse:** `python3 -c "import yaml; yaml.safe_load_all(...)"` on `{secrets,docker-compose,docker-compose.swarm,shared-config}.{yaml,yml}` — 12/12 OK.
- **Docker build:** `docker compose build service` from `yral-rishi-agent-conversation-turn-orchestrator/` — exit 0; image `yral-rishi-agent-conversation-turn-orchestrator-service:latest` built and tagged. (The three spawned services share an identical Dockerfile / pyproject.toml / app/ tree except for project.config string values; one rep build proves the template's Dockerfile + Python deps install path.)
- **FastAPI app import (inside built image):** `docker run --rm --entrypoint python ...:latest -c "from app.main import app; print(...)"` — exit 0, `app` object resolves, default routes `['/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc']` registered. Satisfies the agent-def "FastAPI default route returns 200" smoke (routes exist + the app object is importable inside the runtime container; full live HTTP serve is gated on the cluster's stateful core, not local laptop dev).

### Constraints touched
- **A1 (relaxed)** — 7-step report above for the three placeholder README removals; substantive content preserved as `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` per A1 spirit. Rishi's typed `continue` 2026-05-18 cited as authorisation.
- **A2.1** — bundled three spawn PRs into one per Rishi's explicit directive (`Bundle into one PR per A2.1 since they share shape`). Total diff is ~60 spawned files × 3 services + 6 content-preservation/LOG/STATE files; spawn output dominates and is mechanical (template copy + string substitution), so reviewable as one PR.
- **B3** — every spawned name matches `^yral-rishi-agent-[a-z][a-z0-9-]*[a-z0-9]$` and is under the 63-char Swarm stack limit (47 / 34 / 49 chars).
- **B4** — service names use full DOLR product vocab ("conversation-turn-orchestrator" not "turn-bot", "soul-file-library" not "system-prompt-store", "influencer-and-profile-directory" not "bot-catalog").
- **B7** — every spawned service inherits the template's file-header / function-WHAT-WHEN-WHY / RELATED-FILES footer conventions; no Session-4 hand-written code in this PR beyond the three `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` files (which carry a provenance header + RELATED FILES footer themselves).
- **F1** — template-first build order honoured: Session 2's template + hello-world spawn closed Phase 0; Session 4's three real-service spawns reuse the SAME `new-service.sh` with zero template modifications.
- **F8** — all three spawned services ship the 8 required docs (`README`, `CLAUDE`, `DEEP-DIVE`, `READING-ORDER`, `RUNBOOK`, `SECURITY`, `WALKTHROUGH`, `GLOSSARY`, `WHEN-YOU-GET-LOST`).
- **F12** — Python 3.12 + FastAPI + asyncio + asyncpg stack inherited unmodified.
- **F16** — three SUBFOLDERS in the monorepo, not three new GitHub repos.
- **I11** — this LOG entry + the same-commit `SESSION-4-STATE.md` update satisfy state-hygiene lint.

### Notes
- **Multi-session collision encountered + worktree-per-session fix:** During the surface-and-wait period before `continue`, Session 3 (parallel agent) checked out its own branch in the main repo checkout, which switched the working tree out from under Session 4. My first `git rm` of the placeholder READMEs landed on Session 3's branch by accident — I reverted those staged deletions via `git restore --staged --worktree` (Session 3's working tree restored to its pre-collision state, no Session 3 work damaged), then created a session-4 worktree at `~/Claude Projects/yral-rishi-agent-worktrees/session-4/` (matching the existing convention used by sessions 1 + 2 at the same path pattern). All Session-4 work from that point lands in the worktree, not the main checkout. Surfaced to Rishi 2026-05-18 — flagged as a coordination gap (Sessions 3 + 4 both started without worktrees; sessions 1 + 2 had them).
- **Agent-definition Day-1 spawn-command drift:** the agent def shows bare suffixes (`conversation-turn-orchestrator`), but `new-service.sh`'s `NAME_PATTERN` requires the full `yral-rishi-agent-` prefix. Used the full names; flagging for coordinator to align the agent def's example commands with the script's actual contract.
- **Substantive Soul-File contracts preserved as `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`:** the orchestrator + soul-file-library placeholders carried real engineering contracts (opaque-bytes rule, layer-order versioning, `cache_control: ephemeral` placement). A follow-up PR may fold these into `DEEP-DIVE.md` / `WALKTHROUGH.md` once each service's real surface is built.
- **Coordinator I9 step deferred:** the spawn script's "Next steps" output reminds the caller to stage each spawned service's `.github/workflows/per-service-ci.yml` at the repo root `.github/workflows/<svc>-ci.yml` (per I9 — coordinator-only path). NOT done in this PR; flagging for coordinator.
- **Next:** Day 2 — orchestrator `run_turn(...)` RPC handler skeleton returning schema-valid stub MessageDto behind a feature flag (per the agent def's Day-2 plan + the parity contract — JSON not SSE on v1).

---

## 2026-05-18 — MILESTONE: Session 4 first-launched by coordinator

### Action
Coordinator scaffolded Session 4's STATE + LOG files before Session 4's first work, per the agent definition's "initially scaffolded by coordinator on first launch" clause. Session 4 has completed Step A (first-launch onboarding context, 11 items) + Step B (I12 resume protocol, 6 steps) and is idle pending Rishi's `continue` to start Day 1.

Session 4 owns three services that together implement v2's conversation-turn business logic:
- yral-rishi-agent-conversation-turn-orchestrator (the LLM turn runner)
- yral-rishi-agent-soul-file-library (Soul File CRUD)
- yral-rishi-agent-influencer-and-profile-directory (catalog + Redis cache)

Day 1 task: spawn all three from Session 2's template via `new-service.sh` (one invocation per service, bundled into a single PR per A2.1 since they share shape).

### Files touched
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-4-STATE.md` (new)
- `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-4-LOG.md` (new — this file)

### Why
Phase 1 launch readiness. State-hygiene lint requires SESSION-N-LOG.md to be updated on every session-N PR; scaffolding upfront means Session 4's first real PR appends to existing files (cleaner lint-passing path matching Sessions 1, 2, 5).

### Test evidence
N/A — meta-scaffolding, no functional change.

### Notes
- Session 4's agent definition: `.claude/agents/session-4-orchestrator.md`
- Codex reviewed Session 4's agent def across 4 rounds on PR #92 (8 total across both Session 3 + Session 4 agent defs); all real catches addressed before merge.
- Critical Codex catches that shaped the day-by-day plan:
  - Return shape: JSON MessageDto on v1 (parity), NOT SSE (would break A16). SSE only on /api/v2/* feature-flagged paths.
  - Safety stack (H5 prompt-injection + H4 crisis + A10 NSFW routing) wired Day 3 BEFORE any real LLM call — NOT deferred to Phase 2.
  - B4 product vocab: "Soul File" not "system prompt" in code/internal naming; only the API path keeps the legacy phrasing for chat-ai parity.
  - A14 STOP-and-ask before any live chat-ai read (Day 7 feature-parity sprint uses committed audit docs + contract fixtures by default).
- Session 3 launched in parallel; we coordinate via cross-session-dependencies.md.
- Phase 1 working target 2026-06-07 per Rishi's stated push date. **NOT a production cutover date** — cutover stays at Rishi's typed-YES discretion per A6.

---

(future entries below as Session 4 works)
