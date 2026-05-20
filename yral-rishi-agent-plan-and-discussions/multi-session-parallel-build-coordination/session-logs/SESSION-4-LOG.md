# Session 4 LOG — Orchestrator + Soul File + Influencer Directory

> Append-only diary. Most recent entries at TOP. Never edit past entries; correct via new entries.

## 2026-05-20 — PR #96 round-5 fixup: idempotency lock failure cleanup (Codex 96-A); 96-B false-positive I6 pushback

### Action
Coordinator routed two Codex round-5 findings on PR #96. One real (96-A — lock-failure cleanup); one verified as a false-positive (96-B — alleged `monkeypatch.setenv` Redis-init race, but the conftest uses `monkeypatch.setattr` to pre-inject fakeredis instead).

### 96-A — Idempotency lock failure cleanup (executed)

**Bug Codex flagged.** Round-4's `acquire_or_check` returns `state="acquired"` and the handler then runs the work + calls `mark_complete`. If anything between those two calls raises (handler bug today; Day-5+ LLM-client transient failure tomorrow; Pydantic validation error on response build), `mark_complete` never runs + the in-progress lock stays in Redis for the full 24-hour F10 dedup TTL. A buggy chat turn locks that idempotency key for 24 hours, blocking every legitimate retry with the same key.

**Fix.**
- Added `release_in_progress_lock(redis_key)` to `app/idempotency.py`. Issues `DELETE redis_key` against Redis; verbose role-comment explains why DELETE (not "mark as failed" — F10 + the contract at `interface-contracts/01-internal-rpc-contracts.md` don't define a "failed" state; A2.1 keeps the dispatch surface at 4 states); why on failure ONLY (mark_complete's `done` payload is what concurrent waiters + retries within the 24h window expect); concurrent-waiter behaviour (a polling waiter loses its view of in-progress state; its next poll-loop iteration treats missing key as a SET-NX miss + eventually returns `in_flight_timeout` to the waiter, which is the correct shape for "original request failed; retry from the top").
- Wrapped the post-acquire window in `app/run_turn.py` in `try / except Exception / raise`. Verbose role-comment explains why the try block starts AFTER `acquire_or_check` (lines before it cannot hold a lock — nothing to release); why release ONLY on failure (don't overwrite the `done` state mark_complete just wrote); why re-raise instead of returning a bespoke 500 envelope (uses FastAPI's default exception handler → Sentry capture + structured-log traceback for free + matches every other 500 surface).
- Best-effort cleanup pattern: if `release_in_progress_lock` ITSELF raises (e.g. Redis is down), we LOG the release failure at ERROR (operator-visible signal that the lock will stick until the 24h TTL) but STILL re-raise the original handler exception so the caller sees a 500.

**Regression test.** Added `test_run_turn_releases_idempotency_lock_when_handler_raises_so_retry_starts_fresh` to `tests/test_run_turn.py`:
- Monkeypatches `app.run_turn.mark_complete` to a `raising_mark_complete_for_failure_test` stub. Patches `app.run_turn` (import-shadowed local reference), NOT `app.idempotency` — same pattern PR #96 round-3's concurrent test + PR #104 round-4's parallel-fetch test established; documented in the test's docstring for future readers.
- POSTs once → asserts HTTP 500.
- Inspects `fake_redis` directly: asserts the computed redis_key is absent (the CORE invariant — without `release_in_progress_lock`, the key would still hold `state=in_progress`).
- Un-patches mark_complete + POSTs AGAIN with the SAME idempotency key → asserts 200 + `response.id` is a freshly-generated UUID (proving fresh execution, not a replay of any cached payload).

### 96-B — Test fixture monkeypatch race (I6 pushback — false-positive)

**Verification.** Re-read `tests/conftest.py` + the round-4 `init_redis()` + the conftest's `_REAL_INIT_REDIS_FOR_TESTS` import-time capture pattern.

**The conftest does NOT use `monkeypatch.setenv` to control Redis init.** Concretely:
- The `fake_redis` autouse fixture calls `monkeypatch.setattr(app_idempotency, "_redis", fake)` to pre-inject a `fakeredis.aioredis.FakeRedis(decode_responses=True)` instance into the module-level `_redis` global BEFORE the TestClient lifespan runs.
- The same fixture also stubs `init_redis` + `close_redis` to empty coroutines via `monkeypatch.setattr(app_idempotency, "init_redis", empty_initialize_redis_for_tests)` — so the FastAPI lifespan's startup hook doesn't overwrite the patched `_redis` with a real connection (the prior round-3 fix established this; round-4 preserved it).
- The round-4 production-fail-closed regression test explicitly sets `app_idempotency._redis = None` THEN calls `_REAL_INIT_REDIS_FOR_TESTS()` (the un-stubbed init_redis captured at conftest module-load time) so the new fail-closed gate fires the way it would on a fresh production process startup.
- The fail-closed gate in round-4 was deliberately moved AFTER the `_redis is not None` short-circuit (see round-4 LOG entry) PRECISELY to avoid the kind of test-order/cache-state race Codex is describing — the gate only fires on the production-fail-closed test's bypass path, never during the autouse fixture's pre-injection path.

**Conclusion.** Codex appears to be reading the round-3 state of the file (before the round-4 short-circuit-ordering fix) — same truncation-diff symptom as PR #104's 104-B + 104-C false-positives. The Settings-injection model is already in place via the `monkeypatch.setattr` + `_REAL_INIT_REDIS_FOR_TESTS` pattern; no code change. I6 pushback.

### Files touched
- `app/idempotency.py` — added `release_in_progress_lock(redis_key)` helper with verbose role-comment (DELETE-not-failed-state rationale + on-failure-only rationale + concurrent-waiter behaviour).
- `app/run_turn.py` — added `release_in_progress_lock` to the F10 imports block + wrapped the post-acquire window in `try / except / raise` with best-effort release-failure logging.
- `tests/test_run_turn.py` — added `test_run_turn_releases_idempotency_lock_when_handler_raises_so_retry_starts_fresh` regression gate.
- `SESSION-4-LOG.md` (this entry).

### Constraints touched
- **F10** — "default-on idempotency" is preserved for the happy + replay + 409 + 503 paths; the failure path now also honours the contract by releasing the lock instead of holding a stale entry for 24h.
- **A2.1** — minimal change: ONE new helper + ONE try/except wrap + ONE new test. Did NOT add a "failed" cache state (would have expanded the 4-state dispatch surface + required contract amendment); did NOT introduce a bespoke 500 envelope (would have created a divergent error surface); did NOT add retries to the release call (best-effort is sufficient — fallback is the 24h TTL).
- **C11** — Sentinel-aware client + production-fail-closed gate from round-4 unchanged.
- **H6** — release-on-failure log site uses hash-prefix (via the existing `_idempotency_key_hash_prefix` helper) just like every other log site in idempotency.py; no raw header value leaks on the failure path.
- **A8** — MessageResponse contract unchanged; 200/409/400/503/500 wire shapes unchanged.
- **I6** — pushback raised on 96-B (false-positive); code change limited to 96-A.

### Notes
- **Best-effort release semantics.** If Redis itself is down at the moment of release, we log + still re-raise the original handler exception. The lock will expire at the 24h F10 TTL on its own. This avoids a second exception drowning out the diagnostic value of the original one; operators see both via the structured-log site `idempotency_lock_release_failed`.
- **Codex truncation surface.** 96-B is the third PR-#96/PR-#104 round in which Codex has re-raised already-closed findings while reading what its review notes flag as a truncated diff. Coordinator handles via manual audit per the same routing as PR #104's 104-B + 104-C.
- **Day-5 implication.** When the LLM client lands, transient upstream failures (rate-limit, timeout, 5xx from OpenRouter/Gemini) become routine. This fix means a retry with the same key naturally re-runs the LLM call instead of getting stuck behind a 24h dangling lock.
- **Next:** PR #96 + PR #104 both land → Day 5 real LLM enablement unblocks per the coordinator's standing plan.

---

## 2026-05-19 — PR #96 round-4 fixup: production-fail-closed + X-User-Id required + UUID-validated key + TTL rename

### Action
Codex re-reviewed PR #96 after my round-3 fixup (commit `fe40fcb`) and flagged **4 NEW security/safety findings**. Single fixup commit on `session-4/orchestrator-run-turn-rpc-handler` addresses all four. All real findings; no I6 pushback raised this round.

**19/19 tests PASSED in 0.07s** (the round-3 14 + 5 new round-4 tests) on Python 3.12.13 inside `python:3.12-slim` with fakeredis.

### Four round-4 blockers addressed

**BLOCKER 1 — Sentinel fail-closed in production.** Previous round-3 fix logged a WARNING on the single-primary fallback path. Codex correctly flagged that a warning is not enforcement — a production deploy with the wrong env var would land silently. Round-4 fix: `init_redis()` now raises `SystemExit` with a CRITICAL log when `environment=="production"` AND `redis_sentinel_enabled==False`. Operator-facing message names the exact env var to flip + the alternative remediation (shared-config.yaml fix). Process refuses to start.

Gate placement chose AFTER the `_redis is not None` short-circuit (NOT before) — the auto-use `fake_redis` fixture in tests pre-injects a FakeRedis instance, so the short-circuit fires first and tests sidestep the production-startup gate cleanly. The production-fail-closed regression test (`test_init_redis_raises_system_exit_in_production_without_sentinel`) explicitly sets `_redis = None` before calling `_REAL_INIT_REDIS_FOR_TESTS()` to reproduce the fresh-startup state. Negative control: `test_init_redis_does_not_raise_in_local_without_sentinel` proves environment="local" doesn't trigger the gate.

(Initial implementation placed the gate BEFORE the short-circuit per the directive's "re-init against mis-configured production" defence-in-depth note — that broke 13 existing tests because TestClient's lifespan setup runs `init_redis` during `client` fixture setup, BEFORE the test body's `monkeypatch.setenv`, which then caches a stale Settings object via the `get_settings()` call inside the fail-closed check. Moving the gate after the short-circuit preserves both test isolation + production safety.)

**BLOCKER 2 — X-User-Id REQUIRED (no "unknown-user" fallback).** Previous round-3 code fell back to an `"unknown-user"` sentinel when X-User-Id was missing. That collapsed the idempotency cache scope: two unrelated callers with missing headers could replay each other's cached responses (cross-tenant data-leak shape). Round-4 fix: missing X-User-Id → 400 + ApiResponse envelope `{success:false, msg:..., error:"user_id_header_required", data:null}`. Verified `unknown-user` is gone from the codebase via grep.

**BLOCKER 3 — Validate X-Idempotency-Key as UUID + log only hash prefix.** Previous round-3 code accepted any string as the header value. A malicious or buggy client could stuff PII or message text into the header — that text then landed in Redis keys + structured logs (H6 violation surface). Round-4 fix:
- Route boundary validates `uuid.UUID(idempotency_key)`; `ValueError` → 400 + envelope `{error:"idempotency_key_invalid_format"}`.
- New helper `_idempotency_key_hash_prefix(redis_key) -> str` returns `sha256(redis_key)[:16]` — the H6-safe log identifier.
- Every log site that previously emitted `redis_key_suffix=key.rsplit(":", 1)[-1]` (8 sites in idempotency.py) now emits `idempotency_key_hash_prefix` instead. Same applies to the validation-failure log path (which hashes the offending header value before logging).
- Empty-string + non-UUID test cases both 400 with envelope.

**BLOCKER 4 — `_IDEMPOTENCY_TTL_SECONDS` → `_IDEMPOTENCY_DEDUP_WINDOW_SECONDS`.** `TTL` is the disallowed B2 abbreviation. Renamed the constant; updated the docstring to explain that the Redis-side TTL is the storage mechanism and the application-level concept is the dedup window. Swept the diff for other 2-3-letter abbrevs from the directive's list (`req`/`res`/`cfg`/`obj`/`tmp`/`val`/`ptr`/`idx`); none surfaced.

### Files touched
- `app/idempotency.py` — production-fail-closed gate (BLOCKER 1); `_IDEMPOTENCY_TTL_SECONDS` → `_IDEMPOTENCY_DEDUP_WINDOW_SECONDS` rename + role-comment; 8 log sites switched from `redis_key_suffix` to `idempotency_key_hash_prefix` via new helper `_idempotency_key_hash_prefix(redis_key)`.
- `app/run_turn.py` — X-User-Id required + 400 envelope (BLOCKER 2); UUID validation for X-Idempotency-Key + 400 envelope on non-UUID (BLOCKER 3); hash-only logging on the invalid-format path; renamed `now_iso` references that linger in comments to `current_utc_timestamp_text`; added `import uuid` + `import hashlib`.
- `tests/conftest.py` — captured `_REAL_INIT_REDIS_FOR_TESTS = init_redis` at module-load (Python import-shadowing pattern; lets the production-fail-closed test bypass the fake_redis stub).
- `tests/test_run_turn.py` — `_DEFAULT_TEST_IDEMPOTENCY_KEY` constant + `_fresh_uuid_key()` helper; renamed every non-UUID idempotency-key literal across 8 sites to fixed test UUIDs (`550e8400-...-4466554400{NN}`); added 5 new tests:
  - `test_run_turn_returns_400_envelope_when_user_id_missing` (BLOCKER 2)
  - `test_run_turn_returns_400_envelope_when_idempotency_key_not_uuid` (BLOCKER 3)
  - `test_run_turn_returns_400_envelope_when_idempotency_key_empty_string` (BLOCKER 3)
  - `test_init_redis_raises_system_exit_in_production_without_sentinel` (BLOCKER 1, J1 HOT)
  - `test_init_redis_does_not_raise_in_local_without_sentinel` (BLOCKER 1 negative control)

### Why
Codex round-4 catches were all real security/safety gaps in the round-3 fix:
- Round-3 Sentinel WARNING wasn't enforcement (BLOCKER 1).
- Round-3 unknown-user fallback collapsed cross-tenant cache scope (BLOCKER 2).
- Round-3 didn't validate header format → PII/text leak surface into Redis + logs (BLOCKER 3).
- Round-3 abbreviation sweep missed TTL (BLOCKER 4).

### Test evidence
pytest inside `python:3.12-slim` with `pip install -e '.[dev]'`:
- **19/19 PASSED in 0.07s** (rootdir=/work, pytest-8.3.4, asyncio AUTO).
- 14 round-3 tests still pass (with idempotency keys updated to UUIDs).
- 5 new round-4 tests cover the fixes:
  - X-User-Id-missing → 400 envelope.
  - X-Idempotency-Key non-UUID → 400 envelope.
  - X-Idempotency-Key empty string → 400 envelope.
  - init_redis SystemExit on production+sentinel=False (HOT-tier J1).
  - init_redis no-raise on local+sentinel=False (negative control).

### Constraints touched
- **C11** — production-fail-closed gate replaces the round-3 WARNING-only path. Single-primary fallback path remains for laptop dev (environment="local") but fails closed in production.
- **F10** — idempotency layer now: header REQUIRED + UUID-validated + atomic SET-NX critical section + fingerprint check + hash-only logging.
- **E6** — X-User-Id required from public-api per the contract (round-4 enforces this at the route boundary; missing header = caller bypassed public-api OR public-api has a wiring bug, both 400-rejectable).
- **H6** — hash-only logging of idempotency-key values + the offending header on validation-failure paths. Raw user-supplied bytes never reach Sentry / Langfuse / structured logs by default.
- **B2** — `TTL` removed from the codebase (was the single remaining 2-3-letter abbrev Codex flagged); `unknown-user` sentinel removed (B-rules + cross-tenant collapse rationale).
- **B7** — every new test carries the WHAT / WHEN / WHY docstring + role-comments on every new helper.
- **I6** — no pushback raised this round; Codex round-4 catches were all grounded + actionable + matched CONSTRAINTS verbatim.
- **J1** — orchestrator HOT-tier; production-fail-closed test marked HOT-tier per directive ("this is a production-deploy safety gate").

### Notes
- **Gate placement decision.** The directive said the production-fail-closed gate "should refuse to start" + suggested placing it BEFORE the `_redis is not None` short-circuit for defence-in-depth on re-init calls. My initial implementation followed that exactly + broke 13 tests because TestClient lifespan setup runs init_redis during `client` fixture setup, triggering `get_settings()` BEFORE the test body's monkeypatch.setenv could land — which cached stale Settings. Moving the gate to AFTER the short-circuit preserves both safety properties: tests pre-inject `_redis` via fixture → short-circuit fires → no settings read; production fresh-start has `_redis = None` → short-circuit doesn't fire → gate runs. The production-fail-closed test explicitly sets `_redis = None` to reproduce production startup. The directive's "re-init against misconfigured prod" defence-in-depth concern is preserved as long as init_redis is called only during lifespan startup (it is — close_redis sets `_redis = None` only on SIGTERM, not on re-init paths).
- **Import-shadowing pattern (repeat from round-3).** Capturing `_REAL_INIT_REDIS_FOR_TESTS = init_redis` at conftest module-load is the same trick the round-3 concurrent test used for `mark_complete` — `from foo import bar` binds a local reference that `monkeypatch.setattr(foo, "bar", ...)` doesn't intercept.
- **Test-input UUID sweep.** Renamed 8 non-UUID idempotency keys across the test file to fixed UUIDs (`550e8400-e29b-41d4-a716-446655440010` through `...0016`). Diff-friendly + deterministic per test run.
- **PR #104 round-3 fixup landed in parallel** — different branch, different service folder, no interference. Both fixups are coordinator-mergeable independently.
- **Next:** Day 5 real LLM enablement (per agent definition) — gated on PR #96 (this fixup), PR #100 (Day-3 safety), PR #104 (round-3 fixup) all merging.

---

## 2026-05-19 — PR #96 round-3 fixup: atomic dedup + C11 Sentinel + B2 abbreviation sweep

### Action
Codex re-reviewed PR #96 after the round-2 fixup (commit `104873d`) and flagged **3 NEW BLOCKERs** (different from round 1). Single fixup commit on `session-4/orchestrator-run-turn-rpc-handler` addresses all three. Coordinator authorised the approaches; PR #98 commit `31d1dac` updated the contract to spec C11 + atomic-dedup + 400-reject verbatim.

**14/14 tests PASSED in 0.05s** (the round-2 12 + 2 new round-3 tests — concurrent-handler-count + 409 envelope) on Python 3.12.13 in `python:3.12-slim` with fakeredis.

### Three round-3 blockers addressed

**BLOCKER 1 — F10 race + silent fallback (TWO sub-fixes):**

*(a) X-Idempotency-Key now REQUIRED.* Removed the server-generated UUID4 fallback path entirely. Missing header → 400 with the ApiResponse-shaped envelope `{success:false, msg:..., error:"idempotency_key_required", data:null}` (NOT a bare HTTPException `detail`). The previous round-2 code's UUID4 fallback deduplicated NOTHING on retry (every header-less retry got a fresh UUID); the round-3 fix forces the caller to send a stable key.

*(b) Atomic dedup via SET NX + fingerprint + poll-on-lock + 409.* Replaced the round-2 GET-then-SET flow with a single `SET key value NX EX 86400` as the critical section. The Redis value is a JSON object: `{state:"in_progress", fingerprint:<sha256>}` initially, then `{state:"done", fingerprint, response:<MessageResponse>}` after the handler completes. New `acquire_or_check(...)` helper returns a typed `IdempotencyDecision` the handler dispatches on:
- `acquired` → proceed + mark_complete
- `replay_done` → return cached payload byte-for-byte
- `fingerprint_mismatch` → 409 envelope (same key, different body)
- `in_flight_timeout` → 503 envelope (50ms × 20 = 1s poll ceiling)

Fingerprint = `sha256(canonical_json(body))` with `sort_keys=True` + compact separators so equivalent JSON dicts produce the same fingerprint regardless of key order.

**BLOCKER 2 — C11 Sentinel-aware client.** Replaced `redis.asyncio.Redis.from_url(...)` with `redis.asyncio.sentinel.Sentinel(...)` (Sentinel discovers current primary at connect time + reconnects on failover). New settings: `redis_sentinel_enabled` (default **False** for laptop dev) + reads `redis.sentinel_master_name` + `redis.sentinel_hosts` from `shared-config.yaml` (per C7 — single source of truth; the Sentinel hostnames were ALREADY declared by Session 1's cluster bootstrap). When flag is OFF, a LOUD startup WARNING fires: `c11_violation_single_primary_redis_no_sentinel` with remediation guidance. PyYAML added to runtime deps (matches soul-file-library's pin).

**BLOCKER 3 — B1/B2 abbreviation sweep on the round-2 fixup code.** Renamed every Codex-flagged identifier:
- `cached_str` → consumed away (the new code stores typed JSON envelopes in `IdempotencyDecision`, not raw cache_str)
- `now_iso` → `current_utc_timestamp_text` in `app/run_turn.py`
- `_noop_init` → `empty_initialize_redis_for_tests` in `tests/conftest.py`
- `_noop_close` → `empty_close_redis_for_tests` in `tests/conftest.py`

Swept the rest of the round-2 diff (commit `104873d`) for stray `str` / `iso` / `noop` abbreviations; the renames above were the full list.

### Files touched

**Rewritten:**
- `app/idempotency.py` — full module rewrite: `IdempotencyDecision` dataclass + `acquire_or_check` (SET NX critical section + poll loop) + `mark_complete` + `compute_request_fingerprint` (SHA-256 of canonical-JSON body) + Sentinel-aware `init_redis` reading from shared-config.yaml + WARNING-on-fallback path. PyYAML loaded for the shared-config read.
- `app/run_turn.py` — dispatches on `IdempotencyDecision.state`; returns ApiResponse-envelope 400/409/503 via `JSONResponse`; removed UUID4 fallback; renamed `now_iso` → `current_utc_timestamp_text`.
- `tests/conftest.py` — added `async_client` fixture (httpx.AsyncClient + ASGITransport) for the concurrent-POST regression test; renamed `_noop_init` / `_noop_close`.
- `tests/test_run_turn.py` — added `_required_headers(...)` helper; added 2 NEW tests (concurrent-handler-count + 409 envelope); rewrote `test_run_turn_returns_400_envelope_when_idempotency_key_missing` for the new envelope-shape contract; updated every 200-expecting test to send the now-required headers.

**Modified:**
- `app/config.py` — added `redis_sentinel_enabled` setting with role comment.
- `pyproject.toml` — added `PyYAML==6.0.2` to runtime deps + `[tool.pytest.ini_options]` block with `asyncio_mode="auto"` (required for the new async concurrent test).
- `SESSION-4-LOG.md` (this entry).

### Why
Codex round-3 review caught real safety gaps in the round-2 fix: (a) header-optional path deduplicated nothing on retry, (b) GET-then-SET race let two concurrent POSTs both execute, (c) no fingerprint check meant a reused key could replay the wrong response for a different body, (d) C11 violation (single-primary Redis). All four addressed in this fixup.

### Test evidence

pytest inside `python:3.12-slim` with `pip install -e '.[dev]'` then `pytest tests/`:
- 14/14 PASSED in 0.05s (rootdir=/work, pytest-8.3.4, asyncio mode=AUTO).
- New round-3 tests:
  - `test_run_turn_concurrent_same_key_same_body_executes_handler_once` — uses `asyncio.gather` to fire two truly-concurrent POSTs via httpx.AsyncClient; spies on `app.run_turn.mark_complete` (NOT `app.idempotency.mark_complete` — Python import-shadowing means run_turn.py's local reference is the one the handler actually calls; documented in the test's import comments); asserts exactly ONE invocation + both responses byte-equal.
  - `test_run_turn_same_key_different_body_returns_409_envelope` — sequential POSTs with same key + different bodies; second returns 409 with `error="idempotency_key_reused_with_different_body"`.
- Rewrote `test_run_turn_returns_400_envelope_when_idempotency_key_missing` to assert envelope shape (replaces an older `idempotency_key_header_is_accepted` test that was now redundant with required-by-default semantics).
- Existing 9 Day-2 + 3 round-2 tests still pass with the new `_required_headers(...)` helper.

### Constraints touched
- **F10** — round-3 atomic-dedup + required-header semantics. 4 test gates: replay-byte-equal, concurrent-single-handler, same-key-different-body-409, default-required-header-400.
- **C11** — Sentinel-aware client + flag-gated fallback + LOUD warning when fallback fires. Production must run `redis_sentinel_enabled=True`; laptop dev keeps working against the docker-compose single-primary.
- **B1 + B2** — renamed `now_iso` / `_noop_init` / `_noop_close` per Codex round-3 BLOCKER 3.
- **C7** — Sentinel host list + master name now read from `shared-config.yaml` (the C7 single-source-of-truth). No env-var duplication for those values.
- **C8** — N/A (no SSH changes).
- **A2.1** — kept the new abstractions tight: ONE dataclass (`IdempotencyDecision`) + 4 functions (`acquire_or_check`, `mark_complete`, `compute_request_fingerprint`, `_load_redis_section_from_shared_config`). No frameworks introduced.
- **H6** — log fields are still allowlist-safe: `redis_key_suffix`, `client_provided_key_was_provided`, `conversation_id`, `user_id`. NEVER the cached payload, NEVER the user_message content.
- **F12** — async-native: `redis.asyncio.sentinel.Sentinel`, `asyncio.sleep`, `asyncio.gather` in the concurrent test.
- **I6** — no I6 pushback raised this round; Sentinel hostnames already in shared-config.yaml so no Session-1 DEP needed. Codex round-3 catches were all grounded + actionable.

### Notes
- **No DEP raised** — shared-config.yaml already had `redis.sentinel_master_name` + `redis.sentinel_hosts` populated by Session 1's cluster bootstrap. No I6 to Session 1 needed.
- **Coordinator PR #98 commit 31d1dac** is the contract source-of-truth this fixup matches. Pulled the contract at that commit for cross-reference.
- **Fakeredis vs Sentinel** — the test fixture (`fake_redis`) monkeypatches `app.idempotency._redis` directly + stubs `init_redis` / `close_redis` to no-ops, so the Sentinel path doesn't fire during tests. The Sentinel client is exercised only in production / staging; laptop dev + tests stay on the single-primary fallback (fakeredis in tests; docker-compose Redis in dev). When Session 1's chaos tests for Redis failover land, that's the integration test surface for the Sentinel client itself.
- **Concurrent test caveat** — the spy MUST live on `app.run_turn.mark_complete` (not `app.idempotency.mark_complete`) because `run_turn.py` does `from app.idempotency import mark_complete` which binds a local reference. Patching the idempotency-module reference doesn't intercept the run_turn-module reference. Documented inline in the test's import block.
- **Next:** Day 5 real LLM enablement (per agent definition) — gated on PR #96 (this fixup) + PR #100 (Day-3 safety) + PR #104 (Day-4 soul-file fixup) all merging.

---

## 2026-05-19 — PR #96 fixup: F10 idempotency + B7 import role comments + DTO→Response rename

### Action
Single fixup commit on `session-4/orchestrator-run-turn-rpc-handler` addressing the three Codex BLOCKERs surfaced overnight on PR #96. Coordinator authorised the approach + cross-referenced coordinator PR #98's f708a49 commit on `coordinator/dep-004-update-rpc-contracts-public-api-to-orchestrator-from-sse-to-json` for the contract update.

12/12 tests PASSED (9 Day-2 regression + 1 multi-modal-fields acceptance + 2 F10 idempotency replay/user-scoping) on Python 3.12.13 in `python:3.12-slim` with `fakeredis==2.27.0` as a new dev dep.

### Three blockers addressed
**BLOCKER 1 — F10 default-on idempotency on `POST /v1/turn`.** Added `app/idempotency.py` with async Redis client lifecycle + key-compute + cache-read/write helpers. Wired into `app/main.py` lifespan (init_redis / close_redis). Handler now: computes user-scoped key `idempotency:orchestrator:run-turn:{user_id}:{idempotency_key}` → reads Redis BEFORE any work → on HIT replays cached MessageResponse byte-for-byte → on MISS processes + caches with 24h TTL. Missing `X-Idempotency-Key` → server-generated UUID4 + structured log marker `client_provided_key=false` for future Langfuse trace correlation. Two new tests: byte-identical replay (same key + same user) + user-scoping (same key but different user_id ≠ collision).

**BLOCKER 2 — B7 import role comments on every import in the 4 new Python files** (`app/run_turn.py` / `app/models/turn.py` / `tests/conftest.py` / `tests/test_run_turn.py`). Each import has a one-line role comment explaining what role this import plays in the file's bigger flow, not just what the import IS. stdlib imports included (`datetime`, `logging`, `typing.Annotated`, `uuid.uuid4`, `json`).

**BLOCKER 3 — Rename `MessageDto` → `MessageResponse`** per Rishi's 2026-05-19 morning decision (DTO not on B2 allowlist; English-naming applies to Python class names). Also added the two new RunTurnRequest fields per the coordinator's PR #98 contract update: `media_urls: list[str] | None` + `client_message_id: str | None`. Wire shape unchanged — only the Python identifier moved. Updated every reference in `app/run_turn.py` + `tests/test_run_turn.py` + docstrings + the renamed happy-path test.

### Files touched
- **Added (1):**
  - `app/idempotency.py` — async Redis client + F10 dedup helpers (init_redis / close_redis / get_redis / compute_idempotency_key / get_cached_response / cache_response). All callsites have B7 role-comments on imports.
- **Modified (6):**
  - `app/models/turn.py` — class rename + 2 new request fields + B7 import role comments + updated docstrings & RELATED FILES footer.
  - `app/run_turn.py` — wired idempotency (cache read → MISS process → cache write), added X-User-Id Header binding, server-side UUID4 fallback for missing X-Idempotency-Key, structured-log markers for client-provided vs server-generated key. B7 role comments on every import.
  - `app/main.py` — imports `init_redis` + `close_redis`; lifespan opens Redis at startup + closes on shutdown; added role comments on the new imports; updated RELATED FILES footer.
  - `app/config.py` — added `redis_url: str = "redis://localhost:6379/0"` setting with role comment.
  - `tests/conftest.py` — added `fake_redis` auto-use fixture (patches `app.idempotency._redis` to fakeredis async instance + stubs init_redis/close_redis to no-ops so TestClient lifespan doesn't try to connect to real Redis). B7 role comments on every import.
  - `tests/test_run_turn.py` — renamed happy-path test; added 1 multi-modal acceptance test + 2 F10 idempotency tests (replay + user-scoping); B7 role comments on imports.
  - `pyproject.toml` — added `fakeredis==2.27.0` to dev deps with role comment.

### Why
Codex PR #96 review flagged F10 violation as a hard BLOCKER (idempotency was accepted-but-ignored; F10 says default-on day 1). B7 + B2 also need to be airtight before merge so Codex + Session 5 contract tests + future readers don't trip on inherited drift.

### Test evidence
pytest inside `python:3.12-slim` with `pip install -e '.[dev]'` then `pytest -v tests/`:
- 12/12 PASSED in 0.05s (rootdir=/work, pytest-8.3.4, asyncio-strict)
- New tests:
  - `test_run_turn_accepts_optional_media_urls_and_client_message_id` — A8 multi-modal-parity fields land cleanly
  - `test_run_turn_same_idempotency_key_replays_cached_response` — proves `id` + `created_at` + full body are byte-equal between two POSTs with same X-Idempotency-Key + X-User-Id (the load-bearing F10 regression gate)
  - `test_run_turn_different_users_with_same_key_do_not_collide` — same key, different X-User-Id → distinct `id` (proves user-scoping in the Redis key)

### Constraints touched
- **F10** — fixed; idempotency is now default-on, Redis-backed, 24h TTL, user-scoped. 2 new tests guard the contract.
- **B7** — every import in the 4 NEW PR-#96 files has a one-line role comment + new file `app/idempotency.py` has the same shape.
- **B1 + B2** — Python class names use English now (`MessageResponse` not `MessageDto`). Module + symbol names everywhere honour the B2 allowlist.
- **A8** — RunTurnRequest now accepts `media_urls: list[str] | None` for multi-modal parity per the updated coordinator contract.
- **C7** — `redis_url` setting in typed config; no hardcoded URL in code.
- **C11** — pgBouncer-style note kept (idempotency.py uses asyncpg-style `statement_cache_size=0` pattern is N/A here, but Sentinel-aware URL noted in the redis_url docstring for Day-5+).
- **D1 + D8** — `redis_url` reads from env; no value in committed files. (Production override via Swarm secret env injection per D1.)
- **F12** — Python 3.12 + asyncio-native `redis.asyncio.Redis`, no sync redis-py blocking the event loop.
- **H6** — log fields are `client_provided_key`, `conversation_id`, `user_id` (opaque), `key_suffix` (just the suffix, not the full key with potentially-leaking values). NEVER the cached payload itself.
- **I6** — accepted the coordinator's decisions on all 3 blockers without pushback; the changes are unambiguous + Codex's grounding was solid.

### Notes
- **Coordinator PR #98 (commit f708a49) cross-referenced** for the contract shape. Once #98 merges to main + this PR rebases, the contract doc + the code will be byte-aligned.
- **fakeredis was the right pick over testcontainers-redis** — F10 dedup is one GET + one SET-with-TTL per request, well inside fakeredis's compatibility surface. Day-5+ if we add Redis Streams / pub-sub we'll revisit; today fakeredis = zero Docker requirement + pure-Python in-memory.
- **Existing 9 Day-2 tests still pass unchanged** (regression gate) — the new conftest fixture is auto-use so existing tests don't need to know about the F10 wiring; they just get a clean fakeredis per test.
- **DEP-004 stays open** until coordinator PR #98 merges to main; coordinator owns that doc fix.
- **Next:** Day-5 real LLM enablement (per agent definition) — once PR #96 (this fixup) + PR #100 (Day-3 safety) + PR #104 (Day-4 soul-file) all land.

---

## 2026-05-18 — Day 2, PR: orchestrator `POST /v1/turn` RPC handler skeleton (JSON, NOT SSE)

### Action
Implemented the Day-2 deliverable per the Session-4 agent definition + Rishi's typed Day-2 green-light 2026-05-18: a schema-valid stub for `POST /v1/turn` in `yral-rishi-agent-conversation-turn-orchestrator`, returning a chat-ai-parity `MessageDto` (NOT SSE — per A16 + the agent def's explicit "plain JSON" directive). Behind two safety gates (`environment != production` AND `enable_run_turn_stub=true`) so the stub cannot leak into production parity-test traffic. 9 tests cover 5 happy + 4 error paths; all green locally on Python 3.12.13 inside the template's Dockerfile-equivalent container.

### Branch
`session-4/orchestrator-run-turn-rpc-handler`

### Files touched (orchestrator service only; B4/B7 honoured throughout)
- **Added:**
  - `yral-rishi-agent-conversation-turn-orchestrator/app/models/__init__.py` (package marker)
  - `yral-rishi-agent-conversation-turn-orchestrator/app/models/turn.py` — `RunTurnRequest` (`conversation_id`, `user_message`; `min_length=1` on both) + `MessageDto` (8 fields, byte-identical to chat-ai's MessageDto per `interface-contracts/00-api-contract.md`)
  - `yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py` — FastAPI `APIRouter` exposing `POST /v1/turn`; two-gate refusal logic; stub returns the literal `[v2 phase-1 day-2 orchestrator stub — real LLM response from day-5]` content per agent def + Rishi green-light
  - `yral-rishi-agent-conversation-turn-orchestrator/tests/__init__.py` (package marker)
  - `yral-rishi-agent-conversation-turn-orchestrator/tests/conftest.py` — `clean_settings_cache` (auto-use; invalidates `@lru_cache` between tests) + `client` (FastAPI `TestClient`) fixtures
  - `yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py` — 9 tests (5 happy + 4 error) following B7 doc shape (WHAT/WHEN/WHY per test; priority order in file)
- **Modified:**
  - `yral-rishi-agent-conversation-turn-orchestrator/app/config.py` — added `enable_run_turn_stub: bool = False` setting with role-comment capturing the two-gate rationale
  - `yral-rishi-agent-conversation-turn-orchestrator/app/main.py` — imported + mounted `app.run_turn.router` BEFORE `RequestIdMiddleware` (Starlette LIFO: middleware sees the request, then routes); updated RELATED FILES footer
  - `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md` — raised DEP-004 (see below)
  - `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-4-STATE.md`
  - `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-4-LOG.md` (this entry)

### Why
Day-2 critical path per the agent definition + Rishi's green-light: the `run_turn` skeleton unblocks Session 3's Day-4 wiring + queues the safety stack (Day 3) and real LLM enablement (Day 5) without changing the route signature. The route only mounts in non-production environments AND only with the explicit feature flag set, so a freshly spawned dev/staging environment serving the stub cannot leak into mobile parity-test traffic by accident.

### Test evidence
- **pytest run** inside `python:3.12-slim` (matches template F12 Python 3.12 pin) with `pip install -e '.[dev]'` then `pytest -v tests/`:
  - `test_run_turn_returns_schema_valid_message_dto_when_both_gates_open` — PASSED
  - `test_run_turn_idempotency_key_header_is_accepted` — PASSED
  - `test_run_turn_request_id_header_is_accepted` — PASSED
  - `test_run_turn_echoes_conversation_id_into_response` — PASSED
  - `test_run_turn_stub_content_matches_documented_placeholder` — PASSED
  - `test_run_turn_returns_503_when_flag_unset_default` — PASSED
  - `test_run_turn_returns_503_when_environment_is_production` — PASSED
  - `test_run_turn_returns_422_when_conversation_id_missing` — PASSED
  - `test_run_turn_returns_422_when_user_message_is_empty_string` — PASSED
  - **9/9 PASSED in 0.04s** (rootdir=/work, configfile=pyproject.toml, plugins=asyncio-0.25.2 + anyio-4.13.0)
- **FastAPI app-import smoke** inside `python:3.12-slim` with `pip install .` then `from app.main import app`: import succeeds; `/v1/turn POST` registered alongside the default `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc` routes.
- **Python syntax** (`python3 -m py_compile`): all 4 new + 2 modified Python files OK.
- **Bash + YAML**: no .sh / .yaml / .yml touched in this PR; no regression risk against earlier syntax checks.

### Constraints touched
- **A2.1** — kept scope tight: ONE route, ONE feature flag, ONE Pydantic-models file, NO new middleware (Day 3 adds safety stack on top), NO database (Day 4 adds soul-file schema), NO LLM client (Day 5). Net new code well under 100 strict-code lines (~80 substantive lines across run_turn.py + models/turn.py + the config.py addition; the rest is B7 doc structure).
- **A8 + A16** — `MessageDto` shape byte-identical to chat-ai's parity contract from `interface-contracts/00-api-contract.md`; response is plain JSON not SSE so the mobile client sees zero schema delta during parity window.
- **B1 + B2** — every name reads as English; only B2-allowlist abbreviations used (`id`, `url`, `api`, `http`, `json`, `uuid`, `app`, `init`).
- **B4** — DOLR product vocab: code + comments NEVER say "system prompt" (only `Soul File`, `AI Influencer`); the file headers + tests refer to the soul-file-library by service name + per its role.
- **B7** — every new file has: file-header block (one-sentence summary, "⭐ START HERE", WHY-it-fits, RELATED FILES footer), function-WHAT/WHEN/WHY blocks, role-comments-not-syntax line comments, functions in priority order (happy paths first, error paths after), RELATED FILES footer.
- **C7** — feature flag in `shared-config.yaml`-or-`config.py`-typed settings layer, not a hardcoded value buried in `run_turn.py`.
- **D4** — `request_id` header is accepted + threaded for Day 3's Langfuse correlation wiring (Day 2 just accepts the header without erroring; trace emission lands when the safety stack does).
- **E1** — handler is pure-Python + zero I/O (no DB, no LLM, no Redis) so the stub's latency is dominated by FastAPI's serialisation. Sets the floor for the orchestrator-side latency target (<100ms p95 per agent def Day-8-14 plan) for future PRs to measure against.
- **F10** — `X-Idempotency-Key` header is accepted (Day-3 PR wires it into Redis dedup per F10).
- **F12** — Python 3.12 verified via Docker test run (no local 3.12 available; falling back to container matches what CI will do).
- **H5 + H4 + A10 deferred to Day 3** — safety stack is the Day-3 deliverable per the agent definition; the Day-2 stub has NO safety middleware yet, hence the two-gate refusal (production-block + flag-off-by-default) protecting against accidental enablement.
- **I11** — same-commit LOG + STATE updates land alongside the code.
- **J1** — orchestrator is HOT-tier (75-80% floor). The 9 tests exercise both gates × both header paths × both body-validation surfaces; combined with the schema-shape happy-path assertion that's broad coverage of every Day-2 surface for a stub-only PR.
- **J2** — zero flakes: no time-dependence beyond `created_at` ISO-format assertion (we assert it ends in `Z`, not a specific timestamp); no unmocked network; no race conditions.
- **J3** — tests follow B7 doc shape (plain-English names, WHAT/WHEN/WHY docstring, file header, priority order, role-not-syntax inline comments).

### DEP-004 raised (coordinator follow-up)
`interface-contracts/01-internal-rpc-contracts.md` (coordinator-owned per Session 4's scope-not-allowed list) still shows the OLDER "POST /turn + SSE response" shape from pre-A16 planning. Session 4 implements the agent-def-specified JSON-MessageDto shape. Raised DEP-004 asking coordinator to update that doc to match the actual contract (proposed text included in the DEP). Session 3's Day-4 integration work reads the doc; if it stays stale, Session 3 might write a streaming consumer + then rewrite.

### Notes
- **Tested in Docker, not local venv:** the laptop has Python 3.9.6 only (no `python3.12` in PATH, no `pyenv` / `uv`). Ran pytest inside a fresh `python:3.12-slim` container bind-mounting the orchestrator folder + `pip install -e '.[dev]'`. Matches Session 1's pattern of using the production container as the test bed where appropriate.
- **One residual deprecation warning** from pytest-asyncio about an unset `asyncio_default_fixture_loop_scope` config option — harmless today (we have zero `@pytest.mark.asyncio` tests in this PR; all tests are sync), but worth setting before the first async test lands (Day 3+ when middleware/LLM tests appear).
- **Codex flags from Day-1 PR #95 acknowledged:** coordinator confirmed both BLOCKER/CONCERN are template-inherited (F9 health endpoints + bridge-script test fixtures); not Session 4's introductions; coordinator queuing as DEPs against Session 2. The Day-2 PR doesn't fix those (out of Session 4 scope; Session 2 owns the template).
- **Next:** Day 3 — safety stack BEFORE any real LLM call. H5 prompt-injection defense classifier (rule-based for Phase 1 → ML for Phase 2) → H4 crisis-detection routing (to Claude with Anthropic safety system) → A10 NSFW routing (`is_nsfw=true` → OpenRouter). All three wired as middleware in front of `POST /v1/turn`; each writes its decision to Langfuse trace metadata; default-deny posture.

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
