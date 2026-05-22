# Cross-Session Dependencies (kanban)
> Sessions raise OPEN deps; coordinator moves to RESOLVED when fixed. RESOLVED stays forever (audit trail).

## OPEN

### DEP-010 — Template fixture filename collides with D8/J5 hygiene (literal `.env.local` in test fixtures shouldn't exist); rename fixture + runtime-copy pattern across template + 3 spawned services

Raised: 2026-05-21 by Session 1 (diagnosed while triaging Day-7 CI-red gate on soul-file-library post-PR-#118); rewritten 2026-05-22 by coordinator after Codex BLOCKERs on PR #121 round 1.

What:    The repo-root `.gitignore:25` is the unscoped glob
         `.env.local`. This is correct + intentional + must NOT
         be weakened — per D8/J5, any file literally named
         `.env.local` is forbidden from being committed (the
         rule's whole purpose is preventing real local secrets
         from accidentally landing in git history).

         But the template ships test fixtures at
         `<service>/scripts/tests/fixtures/valid/.env.local`
         (and similar paths) — using the literal filename
         `.env.local` for fixtures collides with the hygiene
         rule by design. When `new-service.sh` spawns a service
         from the template, the spawned `.env.local` fixture
         is copied to disk but then silently dropped on `git
         add` because of the gitignore rule. The spawned
         `secrets.yaml` (sibling fixture) commits cleanly;
         only the `.env.local`-named files are swallowed.

         Per-service `scripts/tests/test_validate_secrets.sh`'s
         happy-path test exercises `validate-secrets.sh` against
         the fixture pair `{secrets.yaml + complete .env.local}`
         and expects exit-0. With `.env.local` missing, the
         validator correctly reports EXIT_MISSING_VALUE → exit 1 →
         the happy-path case fails. The other 4 negative-path
         cases still pass because the validator does the right
         thing — only the happy path needs the present-and-
         populated fixture pair.

         Per-service repo-wide audit (`git ls-files --error-unmatch
         <svc>/scripts/tests/fixtures/valid/.env.local`):

         | Service                                                | valid/.env.local tracked? | CI on main           |
         |---                                                     |---                        |---                   |
         | yral-rishi-agent-new-service-template                  | YES (force-added)         | N/A — no CI          |
         | yral-rishi-agent-conversation-turn-orchestrator        | YES (force-added)         | green                |
         | yral-rishi-agent-soul-file-library                     | NO                        | red since ≥07:01Z    |
         | yral-rishi-agent-public-api                            | NO                        | red since 2026-05-20 |
         | yral-rishi-agent-influencer-and-profile-directory      | NO                        | red since ≥07:17Z    |

         Template + orchestrator have force-added the fixture
         (likely via `git add -f` when the author noticed the
         miss). The other 3 spawned services don't — the bug
         silently cascaded into them at spawn time. Note:
         the force-added entries on template + orchestrator
         are ALSO hygiene violations under D8/J5; they just
         don't fail CI because the fixture is present. They
         need to be migrated to the renamed-fixture pattern
         too.

Why:     Session 1 hit this on 2026-05-21 while diagnosing why
         the post-PR-#118 ci-yral-rishi-agent-soul-file-library
         workflow_dispatch run came back YELLOW (shell-tests job
         FAILED while docker-build + docker-push-to-ghcr both
         succeeded). We proceeded with the soul-file alembic
         operator-action despite YELLOW CI because the failing
         job was unrelated to the runtime artifact + the failure
         was pre-existing (not caused by PR #118). See
         SESSION-1-LOG.md PR #119 entry's Section 5 ("Pre-existing
         CI-red disclaimer") for the precedent-setting paragraph
         that documents this proceed-with-yellow decision.

         The CI signal is real even if non-blocking: 3 of 4
         spawned services have red CI on main, and that's
         actively eroding the value of CI as a quality signal
         across the v2 build. Every future PR to those 3
         services lands against a red-on-main baseline, making
         it harder to detect new failures.

         Coordinator's first draft of this DEP (committed as
         PR #121 round 1) proposed scoping the `.gitignore`
         rule with a path-exempt negation pattern + force-
         adding the fixtures. Codex BLOCKER-correctly flagged
         both: (a) scoping the rule weakens D8/J5 hygiene
         exactly where it matters; (b) routing Session 2 to
         edit fixture files inside Session 3/4 service
         folders is a scope violation. This rewrite addresses
         both — no `.gitignore` edit + per-owner routing.

Scope —  Per-owner fix routing. NO `.gitignore` edit.
who fixes:

         **A1 hard-stop applies to every fixture migration
         in this DEP.** The literal filename `.env.local` is
         in A1's env/config/secrets-shaped hard-stop class
         (CONSTRAINTS.md A1) — even when the contents are
         fixture data, not real secrets. EACH affected
         session's rename/migration PR MUST:

         - Obtain Rishi's typed YES BEFORE staging the
           `git rm` / `git mv` step for any tracked
           `.env.local` path (one typed YES covers the full
           rename + migration for that one service's fixture
           set; does NOT need a fresh YES per file).
         - Include in the PR body the FULL A1 deletion
           safety report in the required format (no
           alternative; A1 treats env/config/secrets-shaped
           files as hard-stop items and the full report is
           required for the old-path deletion that any
           rename implies):
              Deletion performed:
              - Deleted:
              - Reason:
              - Safety checks performed:
              - References checked:
              - Why this was safe:
              - Tests/builds run:
              - Rollback plan:
           If the operation is purely a content-preserving
           `git mv` (path-preserving move, reversible via
           `git mv` in the other direction), the PR body
           MAY additionally note that fact to clarify the
           operation's nature — but the additional note
           does NOT substitute for the A1 deletion report
           above.
         - Verify post-merge that the literal `.env.local`
           path is gone from the tracked tree for that
           service (no leftover under `git ls-files`).

         **Session 2 (template — root of the fix tree)**:
         - Rename the fixture file in the template from
           `.env.local` to `env.local.fixture` (or another
           name not matching the D8/J5 hygiene rule —
           `env.local.fixture` is the suggested form; final
           choice up to Session 2).
         - Update `scripts/tests/test_validate_secrets.sh` in
           the template to copy `env.local.fixture` → a temp
           `.env.local` inside a temp directory at test
           runtime (e.g. `mktemp -d`); run the validator
           against that temp dir; cleanup at test end. The
           literal `.env.local` filename never exists in the
           checked-in tree but DOES exist transiently when
           the validator runs.
         - Update `new-service.sh` (or its post-spawn
           verification step) to assert post-spawn that
           `env.local.fixture` IS tracked in git for the
           spawned service. Fail the spawn loudly if not —
           that catches future cases where the rename pattern
           drifts.
         - Migrate the template's currently-force-added
           `.env.local` fixture file to the new
           `env.local.fixture` filename; remove the literal
           `.env.local` from the template's tracked tree.

         **Session 3 (public-api)**:
         - After Session 2's template change lands, backport
           the rename + runtime-copy pattern into public-api
           (move `env.local.fixture` to be tracked; update
           public-api's `scripts/tests/test_validate_secrets.sh`
           to match the template's new runtime-copy approach).
         - This restores green CI for public-api.

         **Session 4 (soul-file-library + influencer-and-
         profile-directory)**:
         - After Session 2's template change lands, backport
           the rename + runtime-copy pattern into both
           services Session 4 owns.
         - Conversation-turn-orchestrator (also Session 4-
           owned) needs its currently-force-added
           `.env.local` migrated to `env.local.fixture` for
           hygiene parity.
         - This restores green CI for soul-file + influencer.

         **Coordinator**:
         - NO `.gitignore` edit. The rule stays as-is.
         - Optionally: add a one-line comment above
           `.gitignore:25` documenting that fixture files
           must use `env.local.fixture` (or equivalent),
           never the literal `.env.local`. Documentation only;
           does NOT change the rule itself.

Blocks:  BLOCKS PR merges + deploy promotions on the three
         affected services (soul-file-library, public-api,
         influencer-and-profile-directory) by default. The
         shell-tests job is red-on-main for those services;
         per I10 / I2 / J4, CI gates must be trusted and
         green for routine work to proceed.

         EXPLICIT EXCEPTION REQUIRED for any work that
         proceeds while affected-service CI is red. Each
         exception must be (a) documented in the proceeding
         PR's body with the specific reason red CI does not
         undermine that PR's claims, AND (b) recorded as a
         one-off, NOT normalized as a recurring pattern. The
         coordinator records each exception in the relevant
         SESSION-N-LOG.md entry's body with the three-part
         justification below.

         One such exception was recorded on 2026-05-21 for
         the soul-file Day-7 alembic operator-action (see
         SESSION-1-LOG.md PR #119 entry's Section 5 — "Pre-
         existing CI-red disclaimer"). That exception was
         based on: (i) failing job unrelated to runtime
         artifact, (ii) failure pre-existed the PR, (iii)
         this DEP routed the fix in parallel. The same
         three criteria are the bar for any future
         exception; absent all three, the default block
         applies.

         Phase 1 work that does NOT touch the three affected
         services (e.g., orchestrator changes — orchestrator
         CI is green) is unaffected by this DEP.

ETA needed: No hard calendar date. Phase 1 close.

Suggested
resolution: Sequential per-owner ordering — Session 2 first
         (template change is the root; nothing else can land
         meaningfully without it), then Sessions 3 + 4
         backport in parallel against the new template
         pattern.

         Sequence:
         1. Session 2: template rename + runtime-copy + spawn-
            time check. Single PR. Test by running the
            template's `test_validate_secrets.sh` against the
            new pattern locally before push. (Template has no
            CI per the audit table; CI green/red doesn't apply
            here.)
         2. Session 3: public-api backport in a separate PR.
            Restores public-api CI to green.
         3. Session 4: soul-file backport + influencer
            backport + orchestrator hygiene migration.
            Coordinator recommends separate PRs by default
            for clarity (one PR per service migration; makes
            each fixture rename + test refactor + post-merge
            CI restoration auditable independently). This
            is a coordinator process recommendation, NOT an
            A2.1 constraint — A2.1 itself only mandates
            stopping for Rishi confirmation when a fix
            exceeds 100 lines, becomes multi-step,
            introduces abstractions/dependencies, or
            otherwise becomes elaborate. If bundling the
            three migrations together would cross any of
            those A2.1 thresholds, Session 4 STOPs and gets
            Rishi confirmation before bundling. The same
            applies to Session 3's public-api backport if
            it expands beyond the single fixture path.
         4. Coordinator (optional): one-line comment on
            `.gitignore:25` documenting the fixture-rename
            requirement.

How spotted:
         Session 1 diagnosis 2026-05-21 morning while
         triaging the soul-file Day-7 CI-red gate after PR
         #118 (alembic.ini bundle) merged. Full diagnosis
         pasted into the coordinator session + captured
         verbatim in SESSION-1-LOG.md PR #119 entry's
         Section 5 + this DEP entry's tables above. Codex
         BLOCKER on PR #121 round 1 (2026-05-22) corrected
         coordinator's first-draft fix proposal — credit to
         Codex for catching both the D8/J5 hygiene collision
         + the scope violation.

### DEP-009 — Session 3 needs to install H5 prompt-injection middleware in public-api (pre-orchestration placement) to satisfy CONSTRAINTS H5 verbatim

Raised: 2026-05-20 by Session 4 (PR #112 — Day-6 safety stack restoration)

What:    Codex PR-#112 round-2 review correctly flagged that PR #112
         mounts the H5 prompt-injection middleware in the
         **orchestrator** (in front of `/v1/turn`) but CONSTRAINTS
         H5 verbatim places it in **public-api**:

             | H5 | Prompt injection defense middleware pre-orchestration.
                  Blocks extraction attempts, logs to Sentry with
                  `type=prompt_injection`, returns safe fallback |
                  🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 |
                  Middleware in public-api; tests include known
                  injection payloads |

         The Mitigation column ("Middleware in public-api") is the
         load-bearing placement spec — H5 is meant to block at the
         ingress before requests reach the orchestrator RPC at all.

         Session 4 cannot edit Session 3's public-api files per I9 +
         the agent-definition split.

Why:     Orchestrator-side H5 (Session 4's PR #112) is real
         defence-in-depth — it catches jailbreaks that somehow slip
         past public-api OR that originate from a non-public-api
         caller (internal compromise scenario). But it does NOT
         protect public-api's own ingress logging + routing layer,
         and a strict H5 reading requires the public-api placement.

         Concretely: a jailbreak hitting public-api today reaches the
         public-api logs + the orchestrator-RPC dispatcher BEFORE
         orchestrator-side H5 fires. Public-api may also expose
         non-`/chat` routes (auth, influencer-list, etc.) that the
         orchestrator-side H5 doesn't see at all.

Blocks:  Does NOT block PR #112 merge — Codex acknowledged
         orchestrator-side H5 is "useful orchestrator-side safety";
         the cross-session work just needs to be tracked. Day-5+6
         together = "AI responds WITH safety on staging" per
         Rishi 2026-05-20 directive; "WITH safety" includes the
         orchestrator wrap.

         BUT before canary traffic + before the H5 constraint can
         be marked "fully satisfied", Session 3 needs the
         public-api-side H5 middleware.

ETA needed: Before public-api canary traffic + before the strict
         H5 sign-off. No hard calendar date.

Suggested
resolution: Session 3 ports the H5 middleware from Session 4's
         `app/middleware/h5_prompt_injection.py` (or equivalent
         pattern set) into public-api's middleware chain. Same
         Sentry `type=prompt_injection` contract; same
         `_INJECTION_PATTERNS` + base64 threshold; same canned
         "I can't help with that." fallback. Easiest path: copy
         the file + adapt the gate-respect logic to public-api's
         own gating shape (no run_turn flags at the public-api
         layer; the gate-respect concern there is different — e.g.
         maintenance-mode / read-only flags).

         Once Session 3 lands public-api-side H5, Session 4's
         orchestrator-side H5 stays as defence-in-depth (per the
         agent definition's "defence-in-depth" framing in the
         Day-3 plan). Both layers active = the H5 constraint is
         fully satisfied at both placements.

How spotted:
         Codex round-2 review on PR #112 (2026-05-20 11:45 UTC)
         BLOCKER on `app/main.py:155` flagging the orchestrator-only
         placement against H5's documented public-api placement.
         Session 4 verified the citation against CONSTRAINTS.md
         row 129 verbatim before raising this DEP.

---

### DEP-008 — Session 1 needs to add GEMINI_API_KEY to bootstrap/secrets-manifest.yaml so Day-5 LLM enablement deploys cleanly

Raised: 2026-05-20 by Session 4 (PR #109 — Day-5 real LLM enablement)

What:    Codex round-2 review on PR #109 raised a D7 BLOCKER:
         GEMINI_API_KEY is declared in
         `yral-rishi-agent-conversation-turn-orchestrator/secrets.yaml`
         per D8 (per-service manifest), but the cluster-level
         declarative secret manifest at
         `bootstrap/secrets-manifest.yaml` (D7) does not have a
         matching entry. D7 verbatim: "Every secret every service
         needs is declared there with metadata (description, source,
         used-by). Bootstrap scripts validate + interactively create
         missing secrets. CI gate refuses deploy if a required
         secret is missing."

         Session 4 cannot edit Session 1's bootstrap-scope files per
         the I9 + agent-definition split (`bootstrap/` is Session 1's
         folder; I9 says cross-session edits route through the
         coordinator).

Why:     Once PR #109 merges + Phase-2 cluster deploy of the
         orchestrator lands, the deploy will fail D7's CI gate
         ("required secret missing") because the bootstrap manifest
         doesn't know about GEMINI_API_KEY.

         The per-service manifest declares the secret + the operator
         action ("set the env var"); the cluster manifest is what
         tells the bootstrap scripts to provision the GitHub Secret +
         the Swarm secret at deploy time. Both are required.

Blocks:  Deploy of the orchestrator service to the cluster with
         `enable_run_turn_real_llm=true`. Does NOT block PR #109
         merge — the merge is staging-cluster-scope per the Option-1
         agreement on the safety stack; full prod deploy waits for
         coordinator-owned safety-stack restoration PR + cutover
         covenant per A6 anyway. But before the FIRST Swarm-deploy
         attempt of the orchestrator, this entry needs to be
         RESOLVED.

ETA needed: Before the first orchestrator Swarm-deploy attempt that
         needs the real-LLM path active. Day 6+ at earliest; no
         hard deadline.

Suggested
resolution: Session 1 (or coordinator on Session 1's behalf) adds
         one entry to `bootstrap/secrets-manifest.yaml`:

             - name: GEMINI_API_KEY
               description: |
                 Google Gemini API key used by
                 yral-rishi-agent-conversation-turn-orchestrator
                 (per its app/llm_client/gemini.py).
               used_by:
                 - yral-rishi-agent-conversation-turn-orchestrator
               source: GitHub Secret → Swarm secret at deploy
               rotation_policy: every 90 days

         Same metadata-only shape as the existing entries in the
         cluster manifest. Day 6+ routing-matrix work will add
         OPENROUTER_API_KEY + ANTHROPIC_API_KEY alongside.

How spotted:
         Codex round-2 review on PR #109 (2026-05-20 10:33 UTC)
         flagged the D7 manifest gap explicitly. Session 4 verified
         the citation against CONSTRAINTS.md row 74 before raising
         this DEP.

---

### DEP-007 — Day-4 directive cites CONSTRAINTS F2; F2 is actually about hetzner-template-freeze, not Soul-File

> **Renumbered DEP-005 → DEP-007 by coordinator (2026-05-20)** — collision with Session 3's existing DEP-005 (health endpoints in template). Tiebreak by PR-number: Session 3's PR #97 < Session 4's PR #104, so Session 3 keeps DEP-005; Session 4's entry becomes DEP-007 (next free slot since DEP-006 is also occupied).

Raised: 2026-05-18 by Session 4

What:    The Day-4 directive Rishi pasted to Session 4 lists eight
         CONSTRAINTS rows to cite verbatim in the PR body:
         "E8 — B4 — F2 — F12 — C7 — D8 — F8 — A2.1". The directive's
         description of F2 is:

             "F2 — this service IS yral-prompt-composer (the README
             §F2 'merges the 4 layers' piece)."

         CONSTRAINTS row F2 verbatim from
         `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`:

             | F2 | Existing `yral-rishi-hetzner-infra-template` is
                  NEVER modified. V2 template forks and evolves
                  independently | 🔒 | No-delete covenant | Existing
                  repo stays frozen; CI doesn't touch it |

         F2 is the hetzner-template-freeze row — has nothing to do with
         the soul-file-library OR a "yral-prompt-composer" component.

Why:     The directive's "README §F2" reference suggests the intent was
         a README.md section number, not a CONSTRAINTS row. Sessions 4
         (this one) + 5 (when contract-tests land) + future readers
         will check `CONSTRAINTS.md` for an F2 row that backs the
         citation and find a mismatch. Per CLAUDE.md guidance to "open
         CONSTRAINTS.md and confirm the row text matches; catching
         coordinator drift mid-flight saves a redo cycle."

Session 4's resolution in this PR: cite **E8** (the actual Soul-File
         row) + **F8** (8 required docs) + **A4** (data port deferred — note: original DEP-005 text said F11; that was wrong, F11 is the feature-flags row and A4 is the actual data-port row; corrected in PR-#104 round-3 fixup)
         + **F3** (schema-per-service) instead. The Day-4 PR body
         does NOT cite F2.

Blocks:  No hard block. Cosmetic doc-drift; future PRs from any
         session reading "cite F2" guidance will hit the same dead-end.

ETA needed: Before the next "cite F2" appears in a session directive.

Suggested
resolution: Coordinator either:
         (a) clarifies the directive intent — was it a README §
             reference (correct as-is, just rename for clarity), OR
         (b) adds a new CONSTRAINTS row that codifies the
             yral-prompt-composer naming + 4-layer-merge intent the
             directive describes, OR
         (c) confirms the directive should have cited a different
             row (e.g. E8) + amends the autonomy-charter to flag
             this kind of citation-drift as a known surface.

---


### DEP-006 — Session 1 to declare Redis Sentinel config in shared-config.yaml so public-api can wire a real async-Sentinel readiness check

**Status: self-resolved 2026-05-19 pending coordinator move to RESOLVED.**

Session 3 self-resolved the technical question on 2026-05-19 (config was already present in `yral-rishi-agent-public-api/shared-config.yaml`'s `redis:` section — `sentinel_master_name: "yral-v2-redis-primary"` + 3 `sentinel_hosts` entries; the round-3 fixup raised the DEP on a stale read). Session 4's PR #96 round-3 verified independently. Coordinator confirmed + signalled to flip /health/ready from the round-3 503 stub to the real async-Sentinel-aware check (shipped in PR #97 round-4, commit 7aaaf6d). Per the kanban convention — sessions raise to OPEN, coordinator moves to RESOLVED — this entry stays in OPEN with the resolution write-up below until the coordinator runs the formal move in a separate PR.

Raised: 2026-05-19 by Session 3

What:    Session 3's PR #97 round-3 fixup landed an F9-honest 503
         fallback for `GET /health/ready` (per Codex round-3 BLOCKER 2
         + coordinator preference). The handler returned envelope-
         shaped 503 with `error="service_unavailable"` and a
         `data.dependencies.redis = "not_yet_implemented"` marker
         until the real async Sentinel-aware Redis readiness check
         could be wired.

         The real check needs two fields in `shared-config.yaml`
         (Session 1 cluster-bootstrap scope):

           redis.sentinel_master_name: <e.g. "yral-v2-redis-primary">
           redis.sentinel_hosts:
             - host:port  (one per Sentinel node, typically rishi-4/5/6)

         The follow-up Session 3 PR (= the round-4 fixup ALREADY
         landed; details in the Status line above):
           1. Read those fields via the YAML loader.
           2. Build a `redis.asyncio.sentinel.Sentinel` client per F12
              + C11 (async-native, Sentinel-aware).
           3. Replace the stub `_check_redis_reachable()` (was
              returning False) with `await sentinel_client.master_for(...).ping()`
              with a 200ms per-call timeout — health probes fail fast,
              not block the event loop.
           4. Flip the readiness probe from default-503 to "200 when
              Sentinel ping succeeds, envelope-shaped 503 when it
              fails."

         Cross-session coordination note: Session 4's PR #96 round-3
         hit the same config + correctly chose NOT to raise an I6
         DEP. Coordinator can fold both threads when running the
         formal move.

Why:    Until DEP-006 was self-resolved:
         - /health/ready returned 503 unconditionally → Swarm rolling-
           update + Caddy `health_uri /health/ready` (per C10) +
           Uptime Kuma (per D5) all saw the service as down →
           Day-5 cluster deploy would have failed the I2 health gate
           + auto-rollback.
         - Day-4A's JWKS cache (PR #101) + Day-4C's idempotency cache
           (PR #103) also need the Sentinel client when they promote
           from plain-redis-URL to C11-compliant Sentinel routing.

Blocks (RESOLVED in practice — pending coordinator-move bookkeeping):
         Day-5 cluster deploy + M0 milestone evaluation for Session 3.

ETA needed: Already resolved technically. Coordinator move-to-RESOLVED
         is a paper trail item.

Suggested
resolution: Coordinator moves this entry from OPEN to RESOLVED in a
         separate PR (the move itself is coordinator scope per the
         kanban contract; sessions append to OPEN only).

Lessons:  (1) Always grep before raising a DEP — `grep -A 30 "^redis:"
         yral-rishi-agent-public-api/shared-config.yaml` would have
         shown the Sentinel config in 1 second + saved a round-trip.
         (2) Cross-check with parallel sessions wrestling the same
         constraint before raising — Session 4 had already verified
         the same config without raising an I6 DEP. Memory entry
         queued to capture this for future-self.

---

### DEP-005 — Session 2 needs to mirror `/health/{live,ready,deep}` in the template (per F9)

Raised: 2026-05-18 by Session 3

What:    Session 3's Day-2 PR ships `app/api/health_routes.py` locally
         in `yral-rishi-agent-public-api/` because the template
         (`yral-rishi-agent-new-service-template/app/`) does not yet
         include health endpoints. F9 requires the three-tier split
         (`/health/live` cheap, `/health/ready` deps-aware,
         `/health/deep` real round-trip) on EVERY service. Codex
         flagged the gap on Session 3 Day-1 PR #94; coordinator
         confirmed it's template-inherited, not Session 3-introduced.

         The local file Session 3 just shipped is intentionally a
         bridge — same FastAPI APIRouter + handler signatures the
         template should adopt. Copy-paste should work:

         `yral-rishi-agent-public-api/app/api/health_routes.py` →
         `yral-rishi-agent-new-service-template/app/health_routes.py`
         (drop the `/api/` subfolder since the template lacks one;
         re-add the subfolder when the template's `app/api/`
         submodule is built — likely never, since the template stays
         minimal per A2.1).

         OR — keep my file in the spawned copy as the canonical
         version and have the template ship `health_routes.py` at the
         top of `app/` with identical shape. Either way, all 13 v2
         services need these endpoints by the time Day-5 cluster
         deploy lands (per I2 + the Swarm rolling-update health gate
         + the rishi-1/2 Caddy `health_uri /health/ready` per C10).

Why:     Without health endpoints on every v2 service:
         - Swarm rolling-update treats every replica as "unhealthy"
           and auto-rolls-back on first deploy (per I2).
         - rishi-1/2 Caddy's `reverse_proxy ... { health_uri /health/ready }`
           (per C10) marks the upstream dead → 502s the request.
         - Uptime Kuma (per D5) shows the service down forever.

         Bridge in place for `yral-rishi-agent-public-api` so my
         Day-5 deploy isn't blocked, but Session 4's three services
         (orchestrator + soul-file-library + influencer-and-profile-
         directory) + Session 5's user-memory-service + the other 8
         deferred services need the template fix.

Blocks:  NOT a hard block on Session 3 (the local bridge works). DOES
         block all OTHER sessions' first cluster deploys until they
         either re-spawn from a fixed template OR back-fill the local
         bridge in each service folder.

ETA needed: Before Day 5 cluster deploy for any service (so Session 4's
         services don't all hit auto-rollback on first deploy attempt).
         Generous estimate: 1-2 days of Session 2 work.

Suggested
resolution: Session 2 adds `app/health_routes.py` to the template
         (mirror of `yral-rishi-agent-public-api/app/api/health_routes.py`,
         minus the `api/` package nesting) + wires
         `app.include_router(health_router)` in the template's
         `app/main.py`. Spawned services pick it up at next spawn.
         For already-spawned services (Session 3 public-api +
         Session 4's three services), back-fill the local bridge OR
         re-spawn fresh.

---

### DEP-004 — Session 4 asks coordinator to update interface-contracts/01-internal-rpc-contracts.md (public-api → orchestrator section) from SSE to JSON-MessageDto

Raised: 2026-05-18 by Session 4

What:    `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md`,
         "public-api → orchestrator" section (lines 14-36 today).
         The current text shows:

             POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/turn
             Response: SSE stream of events
               event: token       data: { delta: "..." }
               event: complete    data: { message: MessageDto }
               event: error       data: { code, message }

         The actual Day-2 implementation (this session, PR opened
         2026-05-18 — `session-4/orchestrator-run-turn-rpc-handler`)
         ships `POST /v1/turn` returning a plain JSON `MessageDto`
         (NOT SSE) per:
           - CONSTRAINTS A8 + A16 (feature parity HARD; mobile sees
             the same JSON shape chat-ai returns today)
           - The Session-4 agent definition's Day-2 plan (verbatim:
             "Return shape is plain JSON MessageDto matching chat-ai's
             existing `/api/v1/.../messages` parity contract — NOT
             SSE. SSE streaming (if added later) lives behind a
             separate `/api/v2/...` feature-flagged path that cannot
             affect mobile parity traffic.")
           - Rishi's typed Day-2 green-light 2026-05-18 with the
             explicit "plain JSON, NOT SSE — A16 parity" directive.

         Proposed update (coordinator-owned file; Session 4 only
         proposes, doesn't edit per its scope-not-allowed list):

             POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn

             Request:
             {
               conversation_id: string,
               user_message: string
             }
             Headers:
               X-User-Id          (forwarded from public-api after JWT validation, per E6)
               X-Idempotency-Key  (per F10)
               X-Request-Id       (per Langfuse correlation, D4)

             Response: JSON MessageDto (matches chat-ai parity, per A16)
               {
                 id: string,
                 conversation_id: string,
                 role: "user" | "assistant",
                 content: string,
                 media_urls: string[] | null,
                 client_message_id: string | null,
                 created_at: string,
                 count_toward_paywall: boolean
               }

             SSE streaming (if added later) lives at a separate
             `POST /v2/turn-stream` path behind a feature flag per
             the Session-4 agent definition; the v1 path stays
             plain-JSON forever for parity stability.

Why:     Session 3's public-api integration on Day 4+ will read
         `01-internal-rpc-contracts.md` to wire its outbound call to
         the orchestrator. If the contract still shows SSE, Session
         3 will write a streaming-response consumer + then have to
         rewrite once the contract aligns with the actual
         implementation. Easier to land the doc update now.

Blocks:  No HARD block — Session 3 reads PR `session-4/orchestrator-
         run-turn-rpc-handler`'s `app/run_turn.py` + `app/models/
         turn.py` directly to see the real contract, so its Day-4
         integration can proceed regardless. But future Codex / new-
         contributor reads of `01-internal-rpc-contracts.md` will
         hit the same JSON-vs-SSE drift unless the doc updates.

ETA needed: Before Session 3's Day-4 public-api → orchestrator wiring
         work (~3-5 days from now).

Suggested
resolution: Coordinator edits the `public-api → orchestrator` section
         of `01-internal-rpc-contracts.md` to reflect the JSON
         response shape (per the "Proposed update" block above).
         Same edit should also update the request fields to match
         the Pydantic models in
         `yral-rishi-agent-conversation-turn-orchestrator/app/models/turn.py`
         landed on `session-4/orchestrator-run-turn-rpc-handler`.

---

---

## RESOLVED

### DEP-003 — Session 2 needs Session 1 to confirm the three cluster overlay network names match the template's `docker-compose.swarm.yml`

Raised: 2026-05-13 by Session 2
Resolved: 2026-05-14 by Session 1 (PR `session-1/align-overlay-names-with-constraints-c3`, Rishi typed YES on Option (a) the same morning)
Resolution: Session 2 was right — Session 1's Day-1-2 drafts (PR #9 + PR #10) used non-CONSTRAINTS overlay names pulled from `V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md` (`yral-agent-public-web-overlay`, `yral-agent-internal-service-to-service-overlay`, `yral-agent-data-plane-overlay`). CONSTRAINTS C3 verbatim is `yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`, and per the CURRENT-TRUTH.md authority chain CONSTRAINTS wins. Renamed across 5 files (`node-bootstrap.sh`, `patroni-stack.yml`, `redis-sentinel-stack.yml`, `langfuse-stack.yml`, `caddy-swarm-service.yml`) plus an in-script role-comment capturing the doc-vs-CONSTRAINTS divergence for future re-readers. The live cluster's wrong-named overlays on rishi-4/5/6 are removed under a narrow A1 carve-out (Rishi typed YES same morning; scope = exactly those 3 names, after verifying zero containers attached) and recreated with the CONSTRAINTS names + `encrypted=true` confirmed cluster-wide. Names now match Session 2's `docker-compose.swarm.yml` exactly; hello-world spawn unblocked.

What:    `yral-rishi-agent-new-service-template/docker-compose.swarm.yml`
         (PR #18, branch `session-2/template-skeleton-compose`) declares
         three `external: true` overlay networks the template's spawned
         services attach to:

         - `yral-v2-public-web`   (edge → service traffic)
         - `yral-v2-internal`     (service → service RPC)
         - `yral-v2-data-plane`   (service → Postgres/Redis/Langfuse)

         These names come straight from CONSTRAINTS C3 (Saikat
         directive 2026-04-23, captured in V2_TEMPLATE_AND_CLUSTER_PLAN
         §1.7). I'm declaring them as `external: true` so the deploy
         fails fast if they don't exist — but that means Session 1's
         cluster-bootstrap stack MUST create them with exactly these
         three names before any service spawned from the template can
         deploy.

         Asking Session 1 to (a) confirm the names are correct in their
         bootstrap scripts, or (b) flag any drift so I can update the
         template before Day 3 spawns hello-world against the cluster.

Why:     Day 3 hello-world spawn-and-verify needs the cluster to have
         these overlays ready. If the names don't match exactly,
         `docker stack deploy` fails with an unhelpful
         "network yral-v2-public-web not found" error. Better to
         reconcile names before Day 3 than to debug it at deploy time.

Blocks:  Day 3 hello-world deploy verification (per template-and-hello-
         world role spec). NOT blocking template-folder PRs (PRs #17, #18,
         and the upcoming PR #19 for configs); those just declare the
         names and don't actually deploy.

ETA needed: Before Day 3 hello-world spawn (~2-3 days from now).

Suggested
resolution: Session 1 grep their bootstrap-scripts-for-the-v2-docker-
         swarm-cluster/ folder for these three overlay names and either
         (a) reply RESOLVED with "names match", or (b) propose new
         names + I update the template's swarm.yml + project.config.

---

### DEP-001 — Session 1 needs scope-lint paths corrected to match real folder layout
Raised: 2026-05-04 by Session 1
Resolved: 2026-05-04 by Coordinator (https://github.com/dolr-ai/yral-rishi-agent/pull/5, merge commit `6093004`)
Resolution: Coordinator updated `SESSION_PATHS[1..5]` in `.github/workflows/lint-scope-violations.yml` so each session's regex now includes the `yral-rishi-agent-plan-and-discussions/` prefix for both the relevant content folders and the per-session log/state/deps files. Session 1 rebased `session-1/sentry-baseline-cron` onto the fix; PR #4 scope-lint should pass on the next run.

What:    Three CI lint paths in `.github/workflows/lint-scope-violations.yml`
         and `.github/workflows/lint-state-hygiene.yml` did not match the real
         folder paths Session 1 must write to per `.claude/agents/session-1-infra-cluster.md`:

         (a) Latency-baseline scripts folder:
             - Spec / lint expected: `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Actual folder lives at: `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Coordinator-decided fix: either prepend the prefix in `SESSION_PATHS[1]`
               (one-line workflow edit) OR move the folder up to monorepo root
               (needs Rishi YES per A1 — moving an existing artifact).

         (b) Session 1's own log + state file paths:
             - Agent spec line 34 says Session 1 may write to "Your own session log + state file"
             - Real paths: `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md`
                          `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md`
             - Neither path was in `SESSION_PATHS[1]` — so any PR that updated them
               failed scope-lint.
             - Same applied to `cross-session-dependencies.md` (this very file)
               which sessions are expected to write OPEN entries to per I11.

         (c) `YRAL_SESSION_ID` env var was not set in this Claude Code session,
             so `.claude/hooks/post-tool-use.sh` (which reads it via
             `${YRAL_SESSION_ID:-coordinator}`) would write commit-trigger
             diary entries to `SESSION-coordinator-LOG.md` instead of
             `SESSION-1-LOG.md`. Until the session is restarted with the
             env var set, Session 1 wrote manual milestone entries
             directly to its own log.

Why:     PR #4 (Day 0.5 Sentry baseline pull) included:
         - Code in (a) — would have failed `lint-scope-violations`
         - Diary entry in (b) under SESSION-1-LOG — would have failed `lint-scope-violations`
         - Required state-hygiene update in (b) — would have failed BOTH lints if
           SESSION-1-LOG was required by `lint-state-hygiene.yml` AND blocked
           by `lint-scope-violations.yml`. Catch-22 until coordinator fixed.

Blocks:  Session 1 PR for sentry-baseline-cron Day 0.5 deliverable. Also
         blocked every future Session 1 PR until paths were reconciled.

ETA needed: Before PR #4 could be merged. Suggested fix was a 5-line
         edit to `.github/workflows/lint-scope-violations.yml` —
         coordinator handled in their own branch (PR #5).

Suggested
resolution: Update `SESSION_PATHS[1]` in lint-scope-violations.yml to:
         ```
         SESSION_PATHS[1]="bootstrap-scripts-for-the-v2-docker-swarm-cluster/|yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md"
         ```
         Apply analogous fix for SESSION_PATHS[2..5]. Separately, set
         `YRAL_SESSION_ID=N` in each session's launch environment so
         the post-tool-use hook routes diary entries correctly.

### DEP-002 — Session 1 hit a bash parser bug in `.claude/hooks/post-tool-use.sh`
Raised: 2026-05-04 by Session 1
Resolved: 2026-05-04 by Coordinator (https://github.com/dolr-ai/yral-rishi-agent/pull/5, merge commit `6093004`)
Resolution: Coordinator quoted the heredoc tag in `.claude/hooks/post-tool-use.sh` so bash no longer tries to balance apostrophes inside the heredoc body. Auto-diary append on commit now lands cleanly; no more hook-blocking errors on `git commit`.

What:    The hook failed on every git commit with:
         `post-tool-use.sh: line 80: unexpected EOF while looking for matching ')'`
         The bug was in the `NEW_ENTRY=$(cat <<ENTRY ... ENTRY)` block —
         specifically the unquoted heredoc tag `<<ENTRY`, which let bash
         try to parse single-quote pairs inside the heredoc body. The
         body contains `there's` and `'s/^/- /'`; bash's parser ended up
         hunting for an unmatched apostrophe and falling off the end.
Why:     The hook is the I11 mechanism that auto-writes commit-trigger
         diary entries to `SESSION-N-LOG.md`. With the hook broken,
         every Session 1 commit emitted a hook-blocking error, and no
         auto-diary entry was appended. Sessions were forced to write
         every entry manually (which is what I did for Session 1's
         first commit `68bc52b`).
Blocks:  Not a hard merge block — commits still succeeded. But every
         Bash tool call that ran `git commit` surfaced the hook error
         to the session, which was noisy and arguably scope-blocking
         for an Auto-mode session expected to commit unattended.
ETA needed: Coordinator fix-by-Session-2 launch (Day 1 end) so other
         sessions did not hit the same surprise on their first commit.
Suggested
resolution: Quote the heredoc tag — change `<<ENTRY` to `<<'ENTRY'` on
         the line that opens the `cat` heredoc. Quoted heredoc tags
         disable variable expansion AND fix the apostrophe-parser
         issue. Then move the `$TIMESTAMP / $COMMIT_SHA / $COMMIT_MSG /
         $FILES_CHANGED` substitutions out of the heredoc and into a
         `printf` call after the heredoc body is captured. Or rewrite
         the block as a series of `echo`/`printf` lines instead of one
         heredoc — eliminates the parsing edge case entirely.

---

## How to use

### Raising a dependency (session-author writes this)
```markdown
### DEP-<3-digit-number> — <short title>
Raised: YYYY-MM-DD by Session N
What:    <specific thing needed, with technical detail>
Why:     <how it unblocks or improves my work>
Blocks:  <which PRs/tasks of mine are blocked, or "no hard block">
ETA needed: <date>
```

### Resolving a dependency (coordinator writes this when fixed)
Move the entry to RESOLVED section, append:
```markdown
Resolved: YYYY-MM-DD by <who> (PR/decision link)
Resolution: <1-line: how it was answered>
```
