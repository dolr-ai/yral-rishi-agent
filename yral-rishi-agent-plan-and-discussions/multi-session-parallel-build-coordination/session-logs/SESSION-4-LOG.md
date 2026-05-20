# Session 4 LOG — Orchestrator + Soul File + Influencer Directory

> Append-only diary. Most recent entries at TOP. Never edit past entries; correct via new entries.

## 2026-05-20 — PR #104 round-5 fixup: DSN B2 abbreviation rename (Codex 104-A); 104-B + 104-C false-positive I6 pushbacks

### Action
Coordinator routed Codex round-5 findings on PR #104. Three items:
1. **104-A** — Codex flagged `DSN` as a B2 banned abbreviation. Real fix needed: round-1's perl sweep handled `db`→`database` but missed `DSN` (different abbreviation, different contexts).
2. **104-B** — Codex flagged B7 import-comment gaps on new Python files. **Verification: false-positive.** Every PR #104 new .py file has the full B7 file-header block + role comments above each import (or import block). I6 pushback below.
3. **104-C** — Codex re-flagged "A1 approval/reporting needed for migration downgrade table-drop". **Verification: false-positive** (third time Codex has flagged this; almost certainly truncation). The A1 DELETION JUSTIFICATION block + SECURITY.md A1 carve-outs section are both present + load-bearing. I6 pushback below.

### 104-A — DSN rename (executed)
Codex correctly identified `DSN` as the next B2-banned abbreviation in our naming surface. The round-1 db→database perl sweep didn't reach it because `DSN` appears in different identifier contexts: env-var names (`POSTGRES_DSN_SOUL_FILE_LIBRARY`), Python identifiers (`postgres_dsn` Settings field + `dsn` local var), pytest fixture names (`postgres_dsn`), and free-standing "DSN" in docstrings + RUNBOOK + SECURITY + alembic.ini + docker-compose.

**Renamed (OUR identifiers):**
- `POSTGRES_DSN_SOUL_FILE_LIBRARY` → `POSTGRES_CONNECTION_STRING_SOUL_FILE_LIBRARY` everywhere (env var declarations, references, secrets.yaml entry, docker-compose env block, alembic.ini comments, RUNBOOK export example, READING-ORDER table row, app/migrations/env.py reads + error messages, app/database.py error message).
- `Settings.postgres_dsn` → `Settings.postgres_connection_string` (+ `validation_alias=POSTGRES_CONNECTION_STRING_SOUL_FILE_LIBRARY`).
- `dsn = settings.postgres_dsn` local in `init_pool()` → `connection_string = settings.postgres_connection_string`.
- pytest fixture `postgres_dsn` → `postgres_connection_string` (consumed by `run_alembic_upgrade` + `database_pool`).
- Free-standing "DSN" in OUR Postgres-context docstrings + comments → "connection string": app/database.py docstring; app/migrations/env.py docstrings + driver-suffix comment; app/config.py Postgres comment block; secrets.yaml notes; alembic.ini START HERE + footer; docker-compose env-block comment; RUNBOOK production-deploy comment; SECURITY threat-table row; tests/conftest.py file-header + fixture docstring + comment + RELATED FILES footer.

**KEPT verbatim (B2 external-name carve-out):**
- `SENTRY_DSN` env var (matches Sentry SDK env var name verbatim — renaming breaks the SDK).
- `Settings.sentry_dsn` field (matches the same Sentry SDK env var).
- `sentry_sdk.init(dsn=...)` kwarg + all Sentry-context "DSN" docstring text.
- `asyncpg.create_pool(dsn=...)` kwarg — asyncpg's API contract; only the IDENTIFIER we pass it is renamed. Added a one-line B2-carve-out comment at each callsite (app/database.py:92 + tests/conftest.py — kept inline).
- docker-compose.swarm.yml `sentry_dsn` Docker Swarm secret name (Sentry context).
- project.config "The DSN itself is a secret" comment — sits inside the SENTRY section header (Sentry context).
- app/sentry_middleware.py + app/request_id_middleware.py — pre-existing template files using "DSN" in Sentry context only; outside PR #104's surface.

**Aggressive grep convergence:** post-sweep `grep -rni dsn yral-rishi-agent-soul-file-library/` returns only the Sentry-SDK + asyncpg-kwarg carve-out lines listed above; no OUR-identifier DSN occurrences remain.

### 104-B — B7 import-comment gaps (I6 pushback — false-positive)

**Verification.** Spot-checked every .py file added by PR #104 — every one has the full B7 file-header block (file-name + ⭐ START HERE + WHY/WHAT + RELATED FILES footer) + one-line role comments above imports:
- `app/database.py` (lines 1-31 file-header, lines 33-38 imports with role comments).
- `app/models/soul_file.py` (lines 1-24 file-header, lines 26-29 imports with role comments).
- `app/repository/soul_file_repository.py` (lines 1-25 file-header, lines 27-32 imports with role comments).
- `app/api/composed_prompt_routes.py` (lines 1-32 file-header, lines 34-44 imports with role comments).
- `app/api/__init__.py` (round-3 fixup added the file-header explicitly).
- `app/composer/__init__.py` + `app/composer/four_layer_composer.py` (round-1 fixup added file-headers).
- `app/migrations/env.py` (lines 1-24 file-header, lines 26-32 imports with role comments).
- `app/migrations/versions/001_initial_schema_and_seed.py` (lines 1-62 file-header including A1 DELETION JUSTIFICATION block).
- `app/migrations/versions/__init__.py` + `app/migrations/__init__.py` + `app/models/__init__.py` + `app/repository/__init__.py` + `tests/__init__.py` (round-1 fixup added file-headers per Codex round-1 BLOCKER 2).

**Conclusion.** No gaps to fix. The flag is consistent with the round-3+ pattern of Codex reading a truncated diff and re-raising round-1's already-closed B7 finding. I6 pushback: no code change.

### 104-C — A1 approval/reporting block (I6 pushback — false-positive)

**Verification.**
- `app/migrations/versions/001_initial_schema_and_seed.py` — A1 DELETION JUSTIFICATION block at lines 37-62 (38 lines of justification + coordinator approval cite + RELATED FILES). Covers: (a) reversibility-of-this-migration framing; (b) operator-action-only invocation; (c) explicit "never automated"; (d) "never on production data"; (e) coordinator approval citation (Rishi 2026-05-19 Option A) with audit pointers to SECURITY.md + the LOG entry.
- `SECURITY.md` — `## A1 carve-outs granted (the standing audit log)` section starts at line 126 with the formal table at lines 130-132. The table row for the Day-4 fixup explicitly enumerates: date (2026-05-19), carve-out, scope, authoriser, and audit pointers (migration file's A1 block + tests/test_schema_migrations.py A1 PROVENANCE block + PR #104 fixup commit). Schema is the same one the coordinator's relaxed-A1 spec lays out.
- `cross-session-deps.md` was not changed in PR #104 (A1 is per-service, recorded in the service's own SECURITY.md per coordinator guidance — cross-session-deps tracks inter-service contract dependencies, not A1 approvals).

**Conclusion.** All three artifacts the directive asked to verify are in place + load-bearing. Third occurrence of Codex re-raising this already-closed finding; matches the round-3+ truncation-false-positive pattern. I6 pushback: no code change.

### Files touched
- `app/config.py` — `postgres_dsn` field → `postgres_connection_string` + `validation_alias` + Postgres comment block.
- `app/database.py` — variable + error message + B2-carve-out comment for asyncpg's `dsn=` kwarg + RELATED FILES footer.
- `app/migrations/env.py` — env var name + 2 docstrings + driver-suffix comment + RELATED FILES footer.
- `tests/conftest.py` — fixture name + 4 docstrings/comments + RELATED FILES footer.
- `tests/test_schema_migrations.py`, `tests/test_composer.py`, `tests/test_repository.py`, `tests/test_api_composed_prompt.py` — fixture parameter renames (perl sweep, no docstring changes).
- `secrets.yaml` — entry name + 2 description comments + notes.
- `alembic.ini` — 3 docstring/comment lines.
- `docker-compose.yml` — env var name + 1 comment.
- `RUNBOOK.md` — export example + production-deploy comment.
- `READING-ORDER.md` — table row referring to the env var name.
- `SECURITY.md` — threat-table row.
- `SESSION-4-LOG.md` (this entry).

### Constraints touched
- **B2** — `DSN` is no longer an OUR identifier anywhere in PR #104. Sentry SDK + asyncpg API references kept as external-name carve-outs (B2's stated exception for third-party API contract names).
- **A2.1** — minimal-scope rename; no behaviour change, no refactor, no new abstractions. Wire format (Postgres connection string content) is byte-identical; only the identifier referring to it changed.
- **D8** — secret name convention preserved: per-service suffix `_SOUL_FILE_LIBRARY` carried through the rename so a leaked secret stays unambiguous about blast radius.
- **I6** — pushback raised on 104-B + 104-C (both false-positives); code change limited to 104-A.

### Notes
- **Codex truncation surface.** 104-B and 104-C are the third round in which Codex has re-raised already-closed PR #104 findings while reading what its own review noted was a truncated diff (PR #105 budget bump was the coordinator's attempt to fix this). Coordinator confirmed PR #105 closed + the truncation BLOCKER will be handled by manual audit, so this fixup does not attempt to widen the diff or restate the round-1 fixes again.
- **Wire format unchanged.** The Postgres connection string value is byte-identical (`postgresql://user:pass@host:port/database`). Only the IDENTIFIER referring to it (env var name, Python field, fixture name, docstring text) changed. No deploy-coordination needed beyond updating Swarm secret name in the secrets store.
- **Coordinator follow-up.** Once Swarm secret store picks up the new env var name, the operator-side update is a one-line `docker secret create POSTGRES_CONNECTION_STRING_SOUL_FILE_LIBRARY ...` + redeploy. Documented in secrets.yaml so the standard rotation playbook covers it.

---

## 2026-05-19 — PR #104 round-4 fixup: composer hot-path latency — parallelize L1+L2+L4 fetches via asyncio.gather

### Action
Codex's remaining real finding on PR #104 was the composer's 4 sequential database reads per chat turn. Once Day-5 real LLM enablement lands and the composer is on the chat hot path, that serialisation eats into the E1 latency budget. Per the directive: parallelize the 3 reads AFTER L3 via `asyncio.gather` while keeping the full Redis cache layer deferred to Day-5+.

**21/21 tests PASSED** (the round-3 20 + 1 new round-4 parallel-fetch concurrency test) — byte-identity × 5 reps + golden-file diff still hold, proving parallel reads change WHEN not WHAT.

### One blocker addressed

**Codex round-4 finding — composer's hot-path latency.** Round-3 (and earlier) `compose()` issued four SEQUENTIAL `await get_current(...)` calls: L3 → L1 → L2 → L4. Round-4 fix: L3 stays first (hard dependency — L2 lookup keys on `layer_3.archetype`); after L3 returns, the remaining three reads execute as one `asyncio.gather(L1_fetch, L2_fetch, L4_fetch)`. One async wait for three reads instead of three sequential awaits — ~3× lower hot-path latency without bringing forward the Redis cache layer.

Exception flow preserved: `asyncio.gather` propagates the first exception + cancels the rest; the downstream `if layer_X is None` checks still fire for each missing row + raise the same `SoulFileDataIntegrityError` as before. The byte-identity property of the composed prompt is unchanged — parallel reads change WHEN we fetch, never WHAT.

### Files touched
- `app/composer/four_layer_composer.py` — added `import asyncio`; swapped 3 sequential awaits for 1 `asyncio.gather(...)` after L3; expanded role-comment explaining the dependency chain (L3 → {L1, L2, L4}) + the E1 rationale + the exception-flow preservation argument.
- `tests/test_composer.py` — added `test_compose_fetches_l1_l2_l4_in_parallel_after_l3` regression gate (per the directive: "asyncio task-counting … rather than timing-based — flaky"). Spy monkey-patches `four_layer_composer.get_current` (the import-shadowed local reference, NOT the repository module's; same pattern as PR #96's `mark_complete` spy). L1/L2/L4 fetches block on an `asyncio.Event`; the test asserts all three are queued + started_count==3 within a tiny grace window BEFORE releasing the event; then verifies the composer completes successfully.
- `SESSION-4-LOG.md` (this entry).

### Why
Codex correctly flagged the serialisation as a real E1 risk. The Day-4 directive explicitly deferred Redis caching to Day-5+, but parallelizing the 3 reads after L3 is a small fix that satisfies E1 today without bringing forward the full cache layer. The byte-identity contract holds: parallel reads change WHEN, not WHAT.

### Optional latency-bound test — I6 pushback, skipped
The directive offered an OPTIONAL benchmark-style test asserting total composer latency stays under a bound (e.g. `end - start < 0.05`). Per the directive's I6 invitation: skipped. An absolute bound on a testcontainers-postgres + fakeredis stack is inherently flaky in CI (cold-start, Docker scheduling jitter, GitHub-Actions runner load all push the tail). The new concurrency test IS the deterministic regression gate — it asserts the parallel-fetch property directly without leaning on wall-clock measurement. A flaky benchmark would generate noise without catching anything the concurrency test doesn't already catch. Coordinator can override + add a benchmark later if a specific budget number lands; today this avoids the false-alarm cost.

### Test evidence
pytest inside `python:3.12-slim` with testcontainers-Postgres + fakeredis:
- **21/21 PASSED** — the round-3 20 + 1 new round-4 parallel-fetch concurrency test.
- Schema migrations: 1/1 — alembic round-trip clean.
- Repository: 7/7 — partial-unique-index still rejects dual-current.
- Composer: 9/9 (8 round-3 + 1 round-4) — byte-identity × 5 reps still holds; golden-file diff still holds; the new parallel-fetch test confirms gather behaviour.
- HTTP routes: 4/4.

### Constraints touched
- **E1** — composer hot-path latency reduced from 4× sequential to 1 + parallel-3 (≈1× plus the longest of the three parallel reads). The "v2 ≥50% faster than chat-ai" target preserved on prefix-heavy turns.
- **A2.1** — minimal change: ONE asyncio.gather + ONE new test. Did NOT bring forward Redis caching (per directive); didn't introduce new abstractions; didn't refactor compose()'s control flow beyond the gather swap.
- **B7** — expanded role-comment on the gather call captures the dependency chain + E1 rationale + exception-flow preservation argument; new test carries WHAT/WHEN/WHY docstring.
- **I6** — pushback on the optional benchmark test (skipped) + flagged in the commit message; no other pushbacks raised.
- **J1** — composer is on the chat hot path (WARM-tier per J1); the new test is a regression gate at WARM coverage tier.
- **Byte-identity contract** (PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md) — verified preserved via the existing 5-rep parametrize test still passing.

### Notes
- **Import-shadowing pattern (third use).** The spy monkeypatches `four_layer_composer.get_current` (the composer module's local reference) NOT `soul_file_repository.get_current`. Same pattern PR #96 used twice already (round-3 `mark_complete` + round-4 `_REAL_INIT_REDIS_FOR_TESTS`). Documented in the test docstring for future readers.
- **Redis cache layer stays deferred to Day-5+.** Per the Day-4 directive's "Out of scope" list, full Redis caching of composed prompts is Day-5+ work. This fixup does not bring it forward.
- **Codex truncation BLOCKER** on PR #104 is coordinator's surface (prompt-budget bump in a separate workflow change). Not addressed in this fixup.
- **PR #96 round-4 fixup landed in parallel** — different branch, different service folder, no interference.
- **Next:** Day 5 real LLM enablement — gated on PR #96 + #100 + #104 all merging clean.

---

## 2026-05-19 — PR #104 round-3 fixup: app/api/__init__.py B7 + F11→A4 citation + db_pool residual

### Action
Codex re-reviewed PR #104 after my round-1 3-blocker fixup (commit `90a2a5b`) and flagged 2 NEW code BLOCKERs plus 1 Codex-infra issue (truncation budget — coordinator's problem). Single fixup commit on `session-4/day-4-soul-file-library-postgres-schema-and-composer` addresses both code blockers. **20/20 tests still PASSED** after the fixes — pure naming + doc work, no behaviour change.

**Major I6 pushback raised in this fixup** — see BLOCKER 2 below: the coordinator's directive describes F11 as "the data port constraint" but CONSTRAINTS F11 is actually **Feature flags custom Postgres-table**; **A4** is the "All data MUST port" row. Fixed the misuse + flagged the framing drift.

### Codex-infra issue (out of scope, coordinator handles)
Codex's first round-3 BLOCKER was that the diff was truncated at the prompt-budget ceiling so Codex could not audit all 37 files. Per the directive that's the coordinator's problem (bump Codex budget OR manual-audit the unread tail). Not in this fixup's scope; flagging here so future readers know why CI may show a Codex truncation BLOCKER even after this fixup lands.

### Two code blockers addressed

**BLOCKER 1 — `app/api/__init__.py` still at 3-line marker.** My round-1 fixup expanded 6 of the 7 Day-4 `__init__.py` files to the full B7 header shape — `app/api/__init__.py` apparently didn't actually land (either the Write didn't apply or the git-add missed it; both `git diff main..HEAD` and the live file showed the 3-line marker shape). Fixed by writing the full B7 header now (matching the round-1 precedent for the other 6 + Day-3's safety/middleware shape):
- one-line summary
- ⭐ START HERE block
- WHY a separate `api` package vs one `routes.py` (3-reason justification)
- WHAT DOES THIS FILE DO AT IMPORT (the "Nothing — Python uses the file's PRESENCE..." pattern)
- Today's contents list
- Day-5+ adds (forward-looking)
- RELATED FILES footer pointing at composer + main + tests + the cross-service contract

**BLOCKER 2 — F11 citation accuracy + stale `db_pool` reference (TWO sub-fixes):**

*(2a) F11 → A4 sweep (I6 pushback territory).* The directive said "F11 is the 'ALL data MUST port' constraint" — but verified directly against `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`:
- **CONSTRAINTS F11** verbatim: *"Feature flags custom Postgres-table, ~200 LOC, polled every 30s, on/off + % rollout."* ← feature flags, NOT data port.
- **CONSTRAINTS A4** verbatim: *"All data MUST port — AI influencers AND user chat history."* ← THIS is the data-port constraint.

So Codex caught a real misuse + the coordinator's framing of how to fix it was ALSO mis-cited. My data-port references should cite **A4** (not F11, not A1). Per CLAUDE.md "Cross-check coordinator's constraint citations; catching coordinator drift mid-flight saves a redo cycle" + per I6 push-back-once-on-likely-wrong-decision.

F11 → A4 replacements landed in these files (data-port context only — `shared-config.yaml` lines 168/171 cite F11 correctly for feature flags and stayed unchanged):
- `app/migrations/versions/001_initial_schema_and_seed.py` (×2 — module docstring + inline comment)
- `app/migrations/versions/__init__.py` (today's contents block)
- `app/composer/four_layer_composer.py` (raised exception message text)
- `DEEP-DIVE.md` Day-4 status section
- `WHEN-YOU-GET-LOST.md` quick-jump entry
- `SESSION-4-STATE.md` LAST-THING-I-DID + NEXT-3 PLANNED ACTIONS (current-state file — editable)
- `cross-session-dependencies.md` DEP-005 (my own open kanban entry — editable by raiser; annotated the original F11 mention as the corrected mis-citation)

Historical LOG entries (the Day-4 first-push entry + the round-1 fixup entry both contain F11 mentions) were NOT edited — LOG is append-only per the file's own preamble ("Never edit past entries; correct via new entries"). This new entry IS the forward correction.

*(2b) Stale `db_pool` reference.* `pyproject.toml:176` had `db_pool` in a `[tool.pytest.ini_options]` comment that the round-1 perl regex missed (the perl pattern matched on `.py` / `.md` / `.yaml` extensions; `pyproject.toml` is `.toml` and was excluded). Renamed to `database_pool`. Re-ran `grep -rnE "\b(db_pool|app\.db|app/db\.py|app_db|_db\.|db_role|db_url)\b"` across the whole soul-file-library tree post-fix; zero residuals.

### Files touched
- `app/api/__init__.py` — full B7 header (matches the other 6 spawn-pkg __init__.py files Day-4 set up).
- `app/migrations/versions/001_initial_schema_and_seed.py` — F11→A4 ×2.
- `app/migrations/versions/__init__.py` — F11→A4 in today's-contents block.
- `app/composer/four_layer_composer.py` — F11→A4 in exception message.
- `DEEP-DIVE.md` — F11→A4 in Day-5+ wiring callout.
- `WHEN-YOU-GET-LOST.md` — F11→A4 in quick-jump entry.
- `pyproject.toml` — `db_pool` → `database_pool` in the [tool.pytest.ini_options] comment.
- `SESSION-4-STATE.md` — F11→A4 in LAST-THING-I-DID + NEXT-3; flagged the original F11 citation as corrected.
- `cross-session-dependencies.md` — DEP-005 entry: F11→A4 with the corrected-citation annotation.
- `SESSION-4-LOG.md` (this entry).

### Why
Codex round-3 caught two real misses from my round-1 fixup: (a) one `__init__.py` didn't actually pick up the B7 expansion, (b) round-1 perl sweep didn't include `.toml`, leaving one `db_pool` mention behind. Plus the F11 citation drift — partly mine (cited F11 in code where A4 was the right row) and partly the coordinator's directive (the round-3 prompt described F11 as the data-port row when it's actually the feature-flags row). Both fixed in this single round.

### Test evidence
pytest inside `python:3.12-slim` with testcontainers-Postgres (TESTCONTAINERS_RYUK_DISABLED=true + --network host + Docker socket mount):
- 20/20 PASSED (no behaviour change post-fixup; same surface as round-1, just renamed/annotated).
- Schema migrations: 1/1 — alembic round-trip clean.
- Repository: 7/7 — partial-unique-index still rejects dual-current.
- Composer: 8/8 — byte-identity × 5 reps still holds.
- HTTP routes: 4/4.

### Constraints touched
- **A4** — the actual data-port row this fixup cites everywhere instead of the previously-incorrect F11. Net new use; previously absent from this PR.
- **F11** — REMOVED everywhere it was citing data-port; KEPT only at `shared-config.yaml:168/171` where it correctly references feature flags.
- **B7** — `app/api/__init__.py` brought to the same standard as the other 6 Day-4 `__init__.py` files.
- **B2** — final `db_pool` residual purged (pyproject.toml comment was the round-1 miss).
- **I6** — TWO pushbacks raised in this fixup's LOG entry: (1) coordinator's directive mis-cited F11 as data-port; (2) my round-1 perl sweep was incomplete (missed .toml + missed one __init__.py).

### Notes
- **I6 pushback on directive framing.** The round-3 directive said "F11 is the 'ALL data MUST port' constraint". CONSTRAINTS.md disagrees — F11 is feature flags, A4 is data port. Surfacing here so the coordinator can correct future-session directives. The fix in code lands as F11→A4 regardless of the framing drift.
- **Codex truncation is coordinator's surface, not Session 4's.** The directive explicitly excluded the truncation BLOCKER from this fixup's scope; the coordinator will bump Codex's prompt budget OR ship a manual audit. CI may still show that BLOCKER until the coordinator-side fix lands.
- **PR #96 round-3 fixup landed in parallel** — different branch, different service folder, no interference.
- **Next:** Day 5 real LLM enablement (per agent definition) — gated on PR #96 + PR #100 + PR #104 all merging clean. Both PR #96 and #104 now have round-3 fixups awaiting Codex re-run + Rishi YES.

---

## 2026-05-19 — PR #104 fixup: db→database rename + B7 __init__ headers + A1 migration justification

### Action
Single fixup commit on `session-4/day-4-soul-file-library-postgres-schema-and-composer` addressing the three Codex BLOCKERs surfaced overnight on PR #104. Coordinator authorised all three approaches.

20/20 tests still PASSED in 3.74s after the rename + B7 headers + A1 docstring addition. No code-behaviour change — pure naming + doc work.

### Three blockers addressed
**BLOCKER 1 — B2 banned `db` abbreviation.** Renamed `app/db.py` → `app/database.py` (git mv); `db_pool` fixture → `database_pool` (everywhere in conftest + tests + RELATED FILES footers + log fields). Updated every `from app.db import ...` → `from app.database import ...` import statement. Bulk perl regex rename across `.py` / `.md` / `.yaml` (BSD sed doesn't honor `\b` word boundary; switched to perl after first pass produced no diff). Verified zero residuals via `grep -rn "app\.db\b\|\bdb_pool\b\|app/db\.py" --include="*.py" --include="*.md" --include="*.yaml" .`. Also cleaned up DSN docstring examples that used `postgresql://user:pass@host:port/db` → `/database`. secrets.yaml `consumed_by` paths now reference `app/database.py`.

**BLOCKER 2 — B7 headers on package-marker `__init__.py` files.** Expanded all 7 Day-4 `__init__.py` files (`app/api/`, `app/composer/`, `app/migrations/`, `app/migrations/versions/`, `app/models/`, `app/repository/`, `tests/`) from a 3-line "package marker" comment to a full B7 header following the Day-3 `app/safety/__init__.py` + `app/middleware/__init__.py` precedent. Each header now carries: one-line summary, ⭐ START HERE block, WHY-this-package-exists rationale, WHAT-DOES-THIS-FILE-DO-AT-IMPORT (consistently "Nothing — Python uses the file's PRESENCE to mark this as a package"), today's contents list, and RELATED FILES footer.

Coordinator note: the template's own `app/__init__.py` has only a partial header (summary + plain-English + RELATED FILES — no ⭐ START HERE, no "what does this file do at import" block). My new `__init__.py` files now match the **Day-3** safety/middleware precedent, which is the higher bar. The template's `app/__init__.py` itself is template-scope (Session 2) and beyond this fixup's reach.

**BLOCKER 3 — A1 deletion path in alembic downgrade.** Per coordinator decision (Rishi Option A 2026-05-19): KEPT `drop_table` in `downgrade()` as standard Alembic reversibility practice; added three audit-trail blocks:
- **(a)** A1 DELETION JUSTIFICATION block in `001_initial_schema_and_seed.py` module docstring — captures the reversibility-not-destruction rationale verbatim per directive.
- **(b)** A1 PROVENANCE block in `test_schema_migrations.py` docstring — explains the round-trip test's `assert not _table_exists(...)` is intentional reversibility verification, NOT an A1 deletion request from the test.
- **(c)** New "A1 carve-outs granted" section in `SECURITY.md` — single-row standing audit log for this service's authorised deletion paths (date / scope / authoriser / audit pointer). First entry = the Day-4 migration downgrade carve-out.

### Files touched
- **Renamed (1):** `app/db.py` → `app/database.py` via `git mv` (history preserved).
- **Modified (substantive content):**
  - `app/database.py` — file header `db.py` → `database.py`; `logging.getLogger("app.db")` → `getLogger("app.database")`; everywhere it self-references.
  - `app/main.py` — `from app.db import` → `from app.database import`; doc references in role-comments + RELATED FILES.
  - `app/migrations/env.py` — RELATED FILES `../db.py` → `../database.py`.
  - `app/migrations/versions/001_initial_schema_and_seed.py` — added A1 DELETION JUSTIFICATION block.
  - `app/repository/soul_file_repository.py` — `from app.db import get_pool` → `from app.database import get_pool` + RELATED FILES.
  - `app/config.py` — DSN example `postgresql://...:port/db` → `:port/database`.
  - `tests/conftest.py` — `db_pool` fixture renamed → `database_pool`; `import app.db as app_db` → `import app.database as app_database`; all references; DSN example URL.
  - `tests/test_schema_migrations.py` — added A1 PROVENANCE docstring; `db_pool` → `database_pool` param.
  - `tests/test_repository.py` — `db_pool` → `database_pool` (8 test signatures + helper).
  - `tests/test_composer.py` — `db_pool` → `database_pool` (4 test signatures + helper).
  - `tests/test_api_composed_prompt.py` — `db_pool` → `database_pool` (2 test signatures + helper).
  - `secrets.yaml` — `consumed_by: - app/db.py` → `app/database.py`.
  - `DEEP-DIVE.md` / `WALKTHROUGH.md` / `RUNBOOK.md` / `GLOSSARY.md` / `READING-ORDER.md` — `app.db.` → `app.database.`; `app/db.py` → `app/database.py`.
  - `SECURITY.md` — added "A1 carve-outs granted" section (1 row).
  - 7 × `__init__.py` — full B7 headers (per the Day-3 precedent shape).
  - `SESSION-4-LOG.md` (this entry).

### Why
Codex PR #104 review flagged 3 hard CONSTRAINTS violations (B2 `db` abbreviation, B7 minimal `__init__.py` headers, A1 deletion path in migration). Coordinator's Option-A on the B2 rename + the A1 carve-out + the standing-audit-log pattern in SECURITY.md keeps the codebase honest under the same naming-rigor decision that drove the orchestrator-side `Dto → Response` rename.

### Test evidence
pytest inside `python:3.12-slim` with testcontainers-Postgres (TESTCONTAINERS_RYUK_DISABLED=true + --network host + Docker socket mount):
- 20/20 PASSED in 3.74s (no behaviour change post-fixup; same tests as PR #104 first push, just exercising the renamed identifiers + new docstrings).
- Schema migrations: 1/1 PASSED (alembic upgrade → downgrade base → upgrade head round-trip; the `drop_table` in downgrade now carries its A1 justification block + the test docstring records the A1 provenance).
- Repository: 7/7 PASSED (renamed `database_pool` fixture works; partial unique index still rejects dual-current via `asyncpg.UniqueViolationError`).
- Composer: 8/8 PASSED (byte-identity × 5 reps still holds — rename was pure surface).
- HTTP routes: 4/4 PASSED.

### Constraints touched
- **B2** — `db` removed everywhere it appeared as a Python identifier or filename; only remaining `db` tokens are inside string literals describing external URL schemes (DSN format docs) which are out of B2 scope per the abbreviation rule applying to OUR naming.
- **B7** — every Day-4 `__init__.py` file now matches the Day-3 safety/middleware precedent shape (summary / ⭐ START HERE / WHY / WHAT-AT-IMPORT / today's contents / RELATED FILES footer).
- **A1** — Standing carve-out granted by Rishi 2026-05-19 for Alembic migration `downgrade()` reversibility; recorded in 3 audit places (migration docstring + test docstring + SECURITY.md standing log).
- **H11** — round-trip migration test (already in place) now self-documents the A1 provenance.

### Notes
- **Partial template-precedent issue.** The template's `app/__init__.py` carries only a 3-section header (summary + plain-English + RELATED FILES — no ⭐ START HERE / no WHAT-AT-IMPORT block). My new `__init__.py` files match the **higher-bar Day-3** precedent (`app/safety/__init__.py`, `app/middleware/__init__.py`) rather than the template's minimal shape. Coordinator may want to bump the template's own `__init__.py` to match — that's Session 2 territory + out of scope for this Session-4 fixup.
- **No DEP raised today.** Both Codex catches were grounded in CONSTRAINTS rows we already follow elsewhere; no doc drift to surface.
- **PR #96 fixup landed in parallel** — different branch, different service folder, no interference. Both fixups are coordinator-mergeable independently.
- **Next:** Day 5 real LLM enablement (per agent definition) — gated on PR #96 + PR #100 + PR #104 all merging.

---

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
