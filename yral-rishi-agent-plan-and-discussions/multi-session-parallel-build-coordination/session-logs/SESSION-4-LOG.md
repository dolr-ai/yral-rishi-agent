# Session 4 LOG — Orchestrator + Soul File + Influencer Directory

> Append-only diary. Most recent entries at TOP. Never edit past entries; correct via new entries.

## 2026-05-20 — Day-6 framing correction #2: "A10 adult-content filter" misnames CONSTRAINTS A10 (Tara routing rule); rename deferred to follow-up PR

Codex PR-#112 round-6 BLOCKER flagged that calling the adult-content
output-filter middleware "A10" misnames the constraint:

  CONSTRAINTS A10 verbatim row 28: "Tara stays on OpenRouter via
  DUAL routing system. llm-client resolves provider in priority
  order: (1) per-influencer-id rule (Tara's row in
  `llm_routing_rule` table) → OpenRouter with her current model;
  (2) `influencer.is_nsfw=TRUE` → OpenRouter (preserves existing
  NSFW routing from yral-chat-ai); (3) archetype/turn-type
  defaults → Gemini Flash + Claude for crisis."

A10 is the **provider routing rule** — NOT an output-side content
filter. The misnaming originated in PR #100's agent-definition
framing ("A10 NSFW filter (output-side)") + propagated through the
codebase via filename + class name + comments.

**Wire-level identifiers KEPT (Session 3 + Sentry depend on them):**
- `X-Safety-Decision: A10` header value.
- `X-Safety-Reason: a10_adult_content_keyword`.

**Code-side rename deferred to a follow-up PR** to keep this PR's
diff bounded (Codex's truncation BLOCKER fires on large diffs +
the rename touches ~10 sites: file rename + class rename + import
sites in main.py + tests + comment sweeps). The follow-up renames:
- `app/middleware/a10_adult_content_filter.py` →
  `app/middleware/adult_content_output_filter.py`
- `A10AdultContentFilterMiddleware` → `AdultContentOutputFilterMiddleware`
- Test names `test_a10_*` → `test_adult_content_*`

The follow-up does NOT touch the wire-level header values (Session
3 + observability tooling already branch on them).

For PR #112 the orchestrator-side adult-content output filter IS in
place + tested; the naming-implies-A10-compliance concern is
acknowledged via this LOG entry. Future readers scanning the
codebase will see this entry's framing before assuming A10 routing
is implemented.

---

## 2026-05-20 — Day-6 wording correction: H5 is PARTIALLY satisfied (orchestrator-side defence-in-depth); full H5 compliance needs DEP-009 (Session 3 public-api ingress)

Earlier Day-6 entry phrased the safety-stack restoration as "Closes Codex PR-#109 BLOCKER 2" — that's true for the orchestrator-side LLM-call guard but is NOT full H5 satisfaction. CONSTRAINTS H5 row 129 verbatim places the middleware in **public-api** (Mitigation column: "Middleware in public-api; tests include known injection payloads"). Session 4 owns the orchestrator; Session 3 owns public-api.

**Corrected framing:**
- **Orchestrator-side H5 (PR #112)** — defence-in-depth + closes Codex PR-#109 BLOCKER 2 ("LLM call must be protected"). Active now; guards every chat turn that reaches the orchestrator.
- **Public-api-side H5 (DEP-009, Session 3)** — the H5-spec'd placement. NOT yet implemented. Required before canary user traffic + before H5 can be marked fully satisfied at sign-off.

Both layers active = the H5 constraint is fully satisfied at both placements. Until then, the orchestrator-side coverage is necessary but not sufficient. PR #112 LOG entry below should be read with this framing.

---

## 2026-05-20 — Day 6: restore H5/H4/A10 safety stack in front of the LLM call

### Action
PR #109 (Day-5 real LLM enablement) merged to main at 10:46 UTC. Day-6 milestone: re-land the H5 → H4 → A10 safety stack that PR #100 had built but auto-closed when PR #96's base branch was cascade-deleted, and wire it in front of the LLM call so a jailbreak / crisis input is short-circuited BEFORE Gemini ever sees it. This closes Codex PR-#109 BLOCKER 2 ("safety stack must be active before real-LLM path") — the regression gate is a new pair of tests asserting the LLM client is NEVER invoked when H5 / H4 fire.

The original PR #100 code is still on the `session-4/day-3-safety-stack-middleware` branch at commit `dbd40c0` (not deleted per the directive's note). Day-6 cherry-picks the 9 safety files from that commit + drift-fixes them against Day-4 + Day-5 main + adds the 2 new LLM-not-invoked regression-gate tests.

### Three pieces

**1. Restored 9 files from `dbd40c0` via `git checkout dbd40c0 -- ...`**
- `app/middleware/__init__.py` — package marker + LIFO ordering ASCII diagram.
- `app/middleware/_body_replay.py` — `read_and_replay_body()` helper that lets H5 / H4 read the request body without consuming it for downstream layers.
- `app/middleware/_safety_audit.py` — `SAFETY_AUDIT_TRAIL` ContextVar + `record()` helper used by the order-verification test (production no-op when the ContextVar is at its `None` default).
- `app/middleware/h5_prompt_injection.py` — 7 regex patterns + base64-blob threshold (>200 chars) for prompt-injection detection. Reason codes `h5_regex_match` / `h5_base64_blob`. H5 includes a `soul file` reveal-probe pattern alongside system-prompt patterns (B4 defence against attackers learning DOLR vocab from public commits — coordinator carve-out from PR #100 stays).
- `app/middleware/h4_crisis_detection.py` — 8 crisis-language regex patterns. Reason code `h4_crisis_language`. False-positive bias per agent definition.
- `app/middleware/a10_nsfw_filter.py` — output-side filter. Drains response body, parses content, rewrites with canned reply on NSFW keyword match. Reason code `a10_nsfw_keyword`. A10 records the synthetic `handler` audit marker between its own entry + exit (coordinator carve-out from PR #100 stays — handler is out-of-scope to modify so A10's wrapping is the right marker location).
- `app/safety/__init__.py` — package marker.
- `app/safety/canned_responses.py` — 3 callables returning `MessageResponse`-shaped dicts (post-PR-#96-round-3 rename from `MessageDto`). H5 + A10 share `"I can't help with that."`; H4 returns the obviously-stub `[v2 phase-1 day-3 crisis response — real helpline copy from product on day-3.5]` per the directive's "must be obviously a stub, not a wrong helpline number" guidance. All three flip `count_toward_paywall=False` per E4.
- `tests/test_safety_stack.py` — the 10 tests that were in PR #100 (clean pass-through, order verification, 5 short-circuit paths, A10 output rewrite, 2 gate-respect paths).

**2. Wired middleware into the request path (`app/main.py`)**
- Imports `H5PromptInjectionMiddleware` + `H4CrisisDetectionMiddleware` + `A10NsfwFilterMiddleware`.
- LIFO `add_middleware` block produces request flow `H5 → H4 → A10 → handler`:
  ```python
  app.add_middleware(A10NsfwFilterMiddleware)        # 1st added → innermost
  app.add_middleware(H4CrisisDetectionMiddleware)    # 2nd added → middle
  app.add_middleware(H5PromptInjectionMiddleware)    # 3rd added → outermost safety
  app.add_middleware(RequestIdMiddleware)            # 4th added → outermost overall
  ```
- Verbose role-comment block above the calls documents the LIFO mapping per B7 + the order-verification test's contract.
- The Day-5 lifespan init/close pairs (Redis + soul-file + LLM clients) stay unchanged — middleware sits OUTSIDE the route handler + doesn't need lifespan wiring.

**3. Drift-fix + 2 new tests**

Drift caught + fixed (the 9 cherry-picked files were Day-3-era; main is Day-4 + Day-5):
- `MessageDto` → `MessageResponse` everywhere (PR #96 round-3 rename). Bulk perl sweep across 4 middleware + 2 safety files + the test file. Final grep -c MessageDto → 0.
- Gate-check in all 3 middlewares: was `or not settings.enable_run_turn_stub`; Day-5 added the parallel `enable_run_turn_real_llm` flag (both flags allow the handler to run; only ALL-off triggers gate-close). All 3 middlewares now read:
  ```python
  gate_closed = (
      settings.environment == "production"
      or not (
          settings.enable_run_turn_real_llm
          or settings.enable_run_turn_stub
      )
  )
  ```
  Documented in each middleware's gate-respect comment block.
- `STUB_CONTENT` literal in `app/run_turn.py` had "real LLM response from day-5" framing that's now obsolete (Day-5 landed). Updated to `"[v2 phase-1 orchestrator stub — diagnostic-only path; real reply via ENABLE_RUN_TURN_REAL_LLM=true]"`. The test_safety_stack + test_run_turn assertions on the literal moved with it.
- Removed the "NOTE: Safety stack (H5/H4/A10) is being re-landed in a parallel coordinator PR..." block from `app/run_turn.py`'s path-select branch + replaced with a B7-shaped comment naming the actual safety stack now in place.

Existing 10 tests in `test_safety_stack.py` updated for:
- PR #96 round-4 X-User-Id + X-Idempotency-Key REQUIRED headers (added `_required_headers()` helper + the existing `_open_both_gates()` helper docstring updated to mention the Day-5 `enable_run_turn_real_llm` flag alongside the stub flag).
- The new STUB_CONTENT literal text.

Two NEW tests for Codex PR-#109 BLOCKER 2 regression gate:
- `test_h5_jailbreak_short_circuits_before_llm_client_is_invoked`
- `test_h4_crisis_short_circuits_before_llm_client_is_invoked`

Both:
1. Enable the real-LLM flag + placeholder ai_influencer_id.
2. Patch `app.run_turn.get_default_llm_client` + `get_soul_file_client` to spies whose methods raise AssertionError with a descriptive message ("LLM client was invoked despite H5 jailbreak input — the safety stack failed to short-circuit. This is the Codex PR-#109 BLOCKER 2 regression.").
3. POST a jailbreak / crisis body.
4. Assert 200 + X-Safety-Decision header + canned reply + `count_toward_paywall=False`.

If safety ever stops short-circuiting (mis-ordered middleware, dropped pattern, regression in `dispatch()`), the spy's AssertionError fires + the test fails LOUDLY. Same import-shadowing pattern PR #96 round-3 established for `mark_complete` + PR #104 round-4 used for `get_current`.

### Test evidence
**52 passed, 1 skipped in 0.19s** inside `python:3.12-slim` with `fakeredis` + `httpx`:
- Day-5 base: 40 tests (the env-gated Gemini integration test is the 1 skip).
- Day-6 add: 12 tests (10 restored from PR #100 + 2 new BLOCKER-2 closure tests).

Order-verification test (`test_clean_message_executes_middlewares_in_documented_order`) asserts the audit trail `["H5_entry", "H4_entry", "A10_entry", "handler", "A10_exit", "H4_exit", "H5_exit"]` matches the documented contract.

### Files touched
- `app/middleware/{__init__,_body_replay,_safety_audit,h5_prompt_injection,h4_crisis_detection,a10_nsfw_filter}.py` — added.
- `app/safety/{__init__,canned_responses}.py` — added.
- `app/main.py` — middleware imports + LIFO `add_middleware` block + comment.
- `app/run_turn.py` — removed Day-5 NOTE comment + updated STUB_CONTENT literal.
- `tests/test_safety_stack.py` — added.
- `tests/test_run_turn.py` — STUB_CONTENT literal assertion updates (3 spots).
- `SESSION-4-LOG.md` + `SESSION-4-STATE.md` — this entry + state update (I11).

### Design carve-outs from PR #100 preserved (coordinator-approved in original review)
- **A10 holds the synthetic `handler` audit marker** between its own entry + exit. Handler is out-of-scope to modify so A10's wrapping is the right marker location.
- **Gate-respect lives inside each safety middleware** (not a separate 4th gate-middleware). A2.1 — avoids duplicating the handler's gate logic in a fourth middleware.
- **H5 includes `soul file` reveal-probe regex** alongside `system prompt` patterns. B4 + public-commits defence.

### Constraints honoured / touched
- **A2.1** — minimal change to the cherry-picked files (drift-fixes only); no broader refactor. The 2 new tests use the same import-shadowing pattern PR #96 + PR #104 established.
- **A10** — abstract LLM client unchanged; safety stack wraps the route at the middleware layer, NOT the LLM client layer. Day 6+ routing matrix can swap providers without re-wiring safety.
- **B2** — drift sweep confirmed no `noop` / `dto` / `dsn` / `db` style abbreviations re-entered via the cherry-pick. `MessageDto` was the only banned-abbreviation residual + got renamed.
- **B4** — H5 catches both `system prompt` AND `soul file` reveal-probes; product vocab respected (no "bot" / "system prompt" in middleware comments).
- **B7** — every restored file's doc tier preserved from PR #100; gate-check comment blocks expanded in-place to document the Day-5 flag.
- **E4** — every safety-canned reply flips `count_toward_paywall=False`. Asserted in all 5 short-circuit / output-rewrite tests.
- **F11** — gate-respect uses the existing feature flags (no new flags added).
- **H4 + H5 + A10** — the three layers are now in place per agent-definition Day-3 + closure of Codex PR-#109 BLOCKER 2.
- **H6** — middleware logs NEVER carry user-message content. Logged fields: safety_layer / reason / conversation_id / user_message_length. Length isn't PII; content is.
- **I11** — LOG + STATE updated same-commit.
- **J1** — orchestrator is HOT-tier. 12 tests (10 + 2 new) for the safety surface.

### Notes
- **Codex PR-#109 BLOCKER 2 closed**: the 2 new tests assert the load-bearing safety property directly. If a future regression mis-orders the middleware, drops a pattern, or breaks `dispatch()`, the spy's AssertionError fires loudly.
- **A10 NSFW classifier**: keyword-list approach stays for Day-6 (per directive's "real A10 ML-based NSFW classifier — Day-3's keyword-list approach stays; classifier swap is a later phase").
- **content-safety-and-moderation RPC**: H4/H5 stay self-contained for Day-6 (per directive's "real content-safety-and-moderation RPC integration — H4/H5 stay self-contained for now; later phase swaps for the moderation service RPC").
- **Day 7+**: per agent definition — Influencer Directory service (different folder, orthogonal to orchestrator+soul-file).

---

## 2026-05-20 — Day 5: real LLM enablement (the AI actually responds)

### Action
PR #96 (Day-2 stub) + PR #104 (Day-4 Soul File Library) merged to main this morning (07:50 + 07:56 UTC). Day-5 milestone: replace the orchestrator's `STUB_CONTENT` literal with a real Gemini call routed through the soul-file-library's 4-layer composed prompt. Per the coordinator directive's "minimum viable AI actually responds" framing, five pieces in this PR.

### CONSTRAINTS citation verification — directive cited D4 / A8 / J1 / E1 / A2.1 / B7
Opened CONSTRAINTS.md verbatim. All six citations check out:
- D4 row 71 (Langfuse self-hosted; LLM trace every call) — used for the Gemini provider's generation span.
- A8 row 26 (feature parity HARD constraint) — `MessageResponse` wire shape preserved verbatim; LLM reply content slots into `.content` without any other field changing.
- J1 row 166 (HOT tier 75-80% floor; orchestrator included) — Day-5 tests added: 4 new gemini-provider + 6 new soul-file-client + 7 new run_turn integration tests.
- E1 row 81 (≥50% faster; 50% latency budget) — drives the 30s Gemini timeout + the 5s soul-file RPC timeout.
- A2.1 row 20 (avoid over-engineering; check in before >100-line solutions) — minimal scope honored: 1 abstract interface + 1 concrete provider + 1 RPC client + handler wire + tests.
- B7 row 46 (3-tier doc standard) — every new file ships the file-header + WHAT/WHEN/WHY + role-comments + RELATED FILES footer.

### Pushback raised pre-code — PR #100 (Day-3 safety stack) was CLOSED not merged
Coordinator directive's step 4(a) said "After safety stack passes (existing H5/H4/A10 middleware chain stays in place — no change to that order)" — but the safety stack files were never merged. PR #100 auto-closed at 2026-05-20T07:50:16Z (two seconds after PR #96 merged, because #100 was stacked on #96's base branch). `app/safety/` + `app/middleware/` are empty in main.

Surfaced to coordinator via `AskUserQuestion`. Coordinator (Rishi 2026-05-20) call: ship Day-5's 5-piece scope as written but treat "safety stack passes" as a no-op. Added the directive's stipulated one-line comment above the LLM call:

```
# NOTE: Safety stack (H5/H4/A10) is being re-landed in a parallel
# coordinator PR (replacement for auto-closed PR #100). Once that
# merges, a small follow-up PR wires the safety middleware in
# front of this LLM call. Day-5 staging-cluster scope is acceptable
# without safety because no production traffic reaches this code
# yet (rishi-4/5/6 only; production stays on chat-ai.rishi.yral.com).
```

Auto-closed PR #100 still exists on GitHub at commit `dbd40c0a` (the safety branch head — coordinator's restoration PR can reuse the diff verbatim). Cause: PR #96 admin-merge deleted PR #100's base branch.

### The five pieces shipped

**1. LLM client abstraction** — `app/llm_client/__init__.py` + `base.py`.
- `LlmClient` ABC with one async method `generate(*, prompt, user_message, temperature, max_tokens) → LlmResponse`. Keyword-only args per B1 readability.
- `LlmResponse` frozen dataclass: content / provider / model / prompt_tokens / completion_tokens / latency_milliseconds.
- `LlmClientTimeoutError` + `LlmClientUpstreamError` — typed exceptions the handler maps to 504/502 envelopes.
- `init_default_llm_client` + `get_default_llm_client` + `close_default_llm_client` — lifespan singleton mirroring the soul-file pattern.

**2. Gemini provider** — `app/llm_client/gemini.py`.
- Uses `google-generativeai==0.8.3` (added to pyproject.toml runtime deps).
- Model id `gemini-2.5-flash` (locked via `_DEFAULT_GEMINI_MODEL_ID` Final constant for git-blame discoverability).
- 30s timeout via `asyncio.wait_for` per E1.
- Langfuse trace span name `llm.gemini.generate` per D4; metadata = provider, model, prompt_tokens, completion_tokens, latency_milliseconds, temperature, max_tokens (and `failure_kind` on the failure-path span).
- Constructor refuses empty `api_key` (fail-fast on half-configured env).
- `GEMINI_API_KEY` declared in `secrets.yaml` per D8: blast_radius=high (quota-burning); rotation 90d.

**3. Soul File RPC client** — `app/soul_file_client.py`.
- `SoulFileClient.compose(*, influencer_id, user_segment) → ComposedPrompt(layered_prompt, version_pin, cache_hit)` matches the locked contract at `interface-contracts/01-internal-rpc-contracts.md`.
- Lifespan-managed `httpx.AsyncClient` singleton (init/get/close trio).
- 5s timeout; typed exceptions `SoulFileInfluencerNotFoundError` (404) + `SoulFileUpstreamError` (5xx/timeout/unparseable).
- Defensive body parsing — missing contract field becomes `SoulFileUpstreamError` (clean 503 envelope) instead of a KeyError crash.

**4. Wire into `run_turn.py`**.
- Replaced "Gate 2: enable_run_turn_stub" with the new two-flag gate: real_llm OR stub. Production gate stays unconditional 503.
- After idempotency `acquired` decision, the path-select branches on `enable_run_turn_real_llm`. Real-LLM path calls `_generate_real_llm_reply()` (new helper); else stub path keeps `STUB_CONTENT`.
- Four new typed `except` branches map SoulFileInfluencerNotFoundError / SoulFileUpstreamError / LlmClientTimeoutError / LlmClientUpstreamError to 404/503/504/502 envelopes. Each calls `_safely_release_lock(...)` (new helper) so a retry starts fresh per the round-5 96-A pattern.
- Five new Settings fields: `enable_run_turn_real_llm`, `gemini_api_key`, `llm_temperature` (0.7 default), `llm_max_tokens` (800 default), `soul_file_library_base_url`, `day_5_placeholder_ai_influencer_id`.
- Lifespan in `app/main.py` adds `init_soul_file_client()` + `init_default_llm_client()` at startup; `close_default_llm_client()` + `close_soul_file_client()` at shutdown (LLM closes first so we stop issuing new RPCs before the downstream client tears down).

**5. Tests (J1 HOT)**.
- `tests/test_llm_client_gemini.py` (new) — 6 tests: prompt+user_message passthrough; LlmResponse field population; missing usage_metadata defensive defaults; timeout → LlmClientTimeoutError; upstream error → LlmClientUpstreamError; constructor empty-key guard. Plus env-gated `test_gemini_client_real_api_round_trip_when_env_flag_set` skipped unless `INTEGRATION_TEST_GEMINI=true` (CI never runs it).
- `tests/test_soul_file_client.py` (new) — 6 tests: typed 200 happy path; query-param shape per contract; 404 → SoulFileInfluencerNotFoundError; httpx.TimeoutException → SoulFileUpstreamError; 5xx → SoulFileUpstreamError; unparseable body → SoulFileUpstreamError; constructor empty-base-url guard.
- `tests/test_run_turn.py` (extended) — 7 new tests: real-LLM path returns LLM content (NOT stub); 504 envelope on timeout; 502 envelope on upstream error; 404 envelope on unknown influencer; 503 envelope on soul-file upstream; both-flags-off still 503s; stub flag still works when only stub flag is set. All existing 20 tests preserved as regression gates.

### Conftest update — stub the new lifespan helpers
`tests/conftest.py`'s `fake_redis` autouse fixture extended to also stub `init_soul_file_client` + `init_default_llm_client` (and their close pairs) to empty coroutines / no-ops. Without this, `init_default_llm_client` calls `get_settings()` at TestClient lifespan startup, pre-fills the lru_cache with default Settings (real_llm=False, etc.), and every Day-5 test's `monkeypatch.setenv("ENABLE_RUN_TURN_REAL_LLM", "true")` silently fails to take effect.

Patched both the source-module references (`app.soul_file_client.init_soul_file_client`) AND the `app.main` imported references — same import-shadowing pattern PR #96 round-3 established for `mark_complete`.

### Files touched
- `app/llm_client/__init__.py` (new)
- `app/llm_client/base.py` (new)
- `app/llm_client/gemini.py` (new)
- `app/soul_file_client.py` (new)
- `app/run_turn.py` (handler + helpers + gate refactor + docstring)
- `app/config.py` (5 new Settings fields)
- `app/main.py` (lifespan init/close pairs for soul-file + LLM clients)
- `secrets.yaml` (GEMINI_API_KEY declaration per D8)
- `pyproject.toml` (google-generativeai==0.8.3 runtime dep)
- `tests/test_llm_client_gemini.py` (new)
- `tests/test_soul_file_client.py` (new)
- `tests/test_run_turn.py` (7 new tests appended)
- `tests/conftest.py` (Day-5 lifespan-helper stubs)
- `SESSION-4-LOG.md` (this entry)

### Constraints honoured / touched
- **A2.1** — minimal scope: 1 abstract interface + 1 provider + 1 RPC client + handler wire + tests. No routing matrix (deferred per directive), no user-segment tracking, no conversation-row lookup, no Redis caching, no E1 latency-bound assertion (test scaffolding present, no numerical bound until cluster).
- **A8** — MessageResponse wire shape unchanged; LLM reply slots into `.content` only.
- **A10** — concrete Gemini client behind the LlmClient interface; consumers in `run_turn.py` depend on the abstraction only. Day 6+ routing matrix can swap clients without touching the handler.
- **B7** — every new file ships the full file-header + WHAT/WHEN/WHY blocks + role-comments + RELATED FILES footer.
- **B4** — DOLR product vocab honored: "AI Influencer", "Soul File" (the L3 row is the per-influencer soul file). No "system prompt" / "bot" in code or comments.
- **D4** — Langfuse trace span on every Gemini call (success + failure); attributes match the directive's piece-2 list verbatim.
- **D8** — GEMINI_API_KEY declared in secrets.yaml; per-service rotation policy.
- **E1** — 30s LLM timeout + 5s soul-file RPC timeout; LLM reply latency surfaces in LlmResponse for future cluster-side benchmark.
- **F10** — F10 dedup contract preserved across all five new error paths (each calls `_safely_release_lock` so a retry starts fresh).
- **F12** — Python 3.12, asyncio-native throughout.
- **H6** — no prompt / user_message content in logs; only metadata (length, token counts, latency, conversation_id, provider, model).
- **I6** — pushback on directive's "safety stack stays in place" assumption (PR #100 was closed); coordinator confirmed Option 1 (proceed without safety wiring; document gap).
- **J1** — 17 new tests added (6 + 6 + 7) for the HOT-tier orchestrator path.

### Notes
- **Day-5 placeholder ai_influencer_id** — operator-configurable via `DAY_5_PLACEHOLDER_AI_INFLUENCER_ID` env. Empty default; the real-LLM path refuses to run until set. Day 6+ replaces this with the conversation-row lookup per directive.
- **Hardcoded `user_segment="new"`** — per directive verbatim. User-segment tracking lands later phase.
- **Real Gemini integration test** — env-gated; OFF in CI. Run manually before each SDK version bump.
- **Stub path stays accessible** — `enable_run_turn_stub=True` keeps the Day-2 literal reply for diagnostics per agent definition.
- **Safety stack restoration** — coordinator parallel PR (per the Option-1 call). Once that merges, a small follow-up PR mounts the H5/H4/A10 middleware in front of `/v1/turn`. No code change to `run_turn.py` expected — middleware sits OUTSIDE the route handler.
- **Day 6** — either (a) provider routing matrix (Tara → OpenRouter; crisis → Claude; NSFW → OpenRouter) per agent-def + memory `reference_yral_chat_v2_llm_routing_tara`, or (b) coordinator-direction depending on Session 3's endpoint needs by then.

---

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
