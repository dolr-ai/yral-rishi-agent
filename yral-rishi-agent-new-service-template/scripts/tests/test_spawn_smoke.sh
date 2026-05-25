#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_spawn_smoke.sh — end-to-end gate that spawns a fresh service from
# the template, builds its image, boots its docker-compose stack, probes
# /openapi.json, and tears everything down. Guards against template-
# rooted bug cascades like 2026-05-22's DEP-010 (fixture rename) + future
# spawn-tree drift.
#
# ⭐ START HERE: 9 ordered steps below. Each step exits non-zero on
# failure with a clear error message + the failing-step diagnostic dump
# (compose ps + last 50 service log lines + last 20 postgres log lines).
# A single EXIT trap guarantees `compose down -v` + temp-directory
# removal even on signal / abort.
#
# WHAT THIS GATE CATCHES
#   - Spawn-tree layout drift (DEP-010-class fixture renames, missing
#     F8 docs, secrets.yaml.template not renamed)
#   - Image-build drift (deprecated base images, Dockerfile regressions
#     like the Bitnami pgbouncer 404 caught by Day-3 PR 5)
#   - Service-skeleton boot drift (middleware load failure, Sentry-init
#     crash, Python import error, env-var typo in compose)
#   - Compose wire-up drift (image tag typos, healthcheck syntax errors)
#
# WHAT THIS GATE DOES NOT CATCH (yet — needs template skeleton expansion
# per DEP-014)
#   - shared-config.yaml Redis sentinel hostname drift (template
#     skeleton's app/main.py does NOT import or connect to Redis)
#   - Redis AUTH client-wiring drift (same root: no Redis client today)
#   - Postgres connection-string drift (same root: no asyncpg client)
#
# WHERE THIS RUNS
#   - GitHub Actions: .github/workflows/template-spawn-smoke.yml
#     triggers on pull_request paths
#     `yral-rishi-agent-new-service-template/**` (+ the workflow itself).
#   - Local mac: `bash <full-path>/test_spawn_smoke.sh` (Docker Desktop
#     must be running). Works from ANY cwd — including folders outside
#     the source repo (e.g. `cd /tmp && bash …/test_spawn_smoke.sh`)
#     — because path resolution below derives both TEMPLATE_ROOT and
#     REPO_ROOT from `dirname "$0"`, and the spawn invocation cd's
#     into REPO_ROOT in a subshell before calling new-service.sh
#     (so the spawner's own `git rev-parse --show-toplevel` resolves
#     correctly).
#
# WHY PORT 8000?
# The template's docker-compose.yml binds host 8000 → container 8000.
# Any local process already on 8000 will collide. Acceptable for CI
# (clean runner each invocation) + dev mac (operator can `lsof -i:8000`
# + stop conflicting service). Randomizing the host port would require
# substituting the compose file at spawn time, which is out of scope
# for this gate.
#
# WHY NOT USE `docker compose up --wait`?
# `--wait` only blocks until healthchecks pass. The template's `service`
# container has no healthcheck (only postgres + redis do). So `--wait`
# would return as soon as uvicorn's process starts — BEFORE it's
# actually serving requests. Polling `/openapi.json` directly is a
# stronger signal: it confirms the ASGI app loaded, every middleware
# initialised, and the route table is live.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Strict mode: errexit + nounset + pipefail. Any unhandled error aborts
# the run; trap below still fires for cleanup.
set -euo pipefail


# ===========================================================================
# Path resolution
# ===========================================================================

# Resolve script-relative paths so the gate works regardless of cwd
# (CI does `bash <full-path>`; operator may run from any folder,
# INCLUDING folders outside the repo entirely — e.g. `cd /tmp && bash
# <full-path>/test_spawn_smoke.sh`).
TESTS_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIRECTORY="$(cd "$TESTS_DIRECTORY/.." && pwd)"
TEMPLATE_ROOT="$(cd "$SCRIPTS_DIRECTORY/.." && pwd)"
# Repo root is one level above the template folder. We resolve it
# here (not via `git rev-parse --show-toplevel` from cwd) because
# the script may be invoked from OUTSIDE the repo — in that case
# `git rev-parse` would fail or resolve to a different repo
# entirely. The new-service.sh invocation below uses this REPO_ROOT
# to `cd` into the repo before spawning, so new-service.sh's own
# `git rev-parse --show-toplevel` resolves the right tree.
REPO_ROOT="$(cd "$TEMPLATE_ROOT/.." && pwd)"
NEW_SERVICE_SH="$SCRIPTS_DIRECTORY/new-service.sh"


# ===========================================================================
# Pre-flight: DEP-010 no-index probe regression-class guard
# ===========================================================================
#
# Run BEFORE step 0 (Docker pre-flight) so the gate fails fast if the
# new-service.sh DEP-010 probe has regressed. Sub-second; no Docker
# needed; entirely sandbox-internal. If this test fails, the spawn
# below would still "pass" superficially while the probe was silently
# broken — so we gate the whole smoke on it.
#
# See yral-rishi-agent-new-service-template/scripts/tests/
# test_dep010_no_index_guard.sh for the 3-assertion shape (the fix
# works + the bug it closes + a static-grep regression-guard on
# new-service.sh's `check-ignore --no-index` usage).
echo "── PRE-FLIGHT 1 ── DEP-010 no-index probe regression-class guard"
if ! bash "$TESTS_DIRECTORY/test_dep010_no_index_guard.sh"; then
    echo ""
    echo "FAIL  DEP-010 no-index probe guard failed — aborting spawn-smoke."
    echo "      The DEP-010 post-spawn probe in new-service.sh is in a"
    echo "      state that would miss the regression class Codex flagged"
    echo "      on PR #135 round-3. Fix new-service.sh's step-6 probe"
    echo "      before re-running the smoke."
    exit 1
fi


# ===========================================================================
# Pre-flight 2: DEP-014 safety-gate pytest run
# ===========================================================================
#
# Codex PR #151 round-3 CONCERN: round-3 added pytest tests for the
# DEP-014 safety gates (production-fail-closed gate +
# sentinel-config-parsing) but the spawn-smoke only checked file-
# existence; the tests were never EXECUTED by the gate. Decorative
# tests rot silently.
#
# This pre-flight actually runs `pytest tests/` against the source
# template + asserts all tests pass. Runs BEFORE step 0 (and the
# expensive Docker compose work) so a regression in the safety
# gates fails fast.
#
# WHY DOCKER FOR PYTEST (not host Python)
# Template's `pyproject.toml` pins `python_requires=">=3.12, <3.13"`.
# CI runners (ubuntu-latest) ship a usable Python 3.12; macOS dev
# default is Python 3.14. `docker run --rm python:3.12-slim` gives a
# consistent runtime + isolates test dependencies from the host. The
# minimum dep set (pytest + pytest-asyncio + redis + pyyaml +
# pydantic-settings) installs in ~5-10s on a warm cache; ~30s on a
# cold cache. Acceptable in exchange for cross-platform reliability.
#
# WHY MINIMUM DEP SET (not the full `.[dev]`)
# The safety-gate tests only touch `app/config.py` + `app/redis_client.py`
# + their transitive imports (pydantic-settings, redis.asyncio, yaml).
# They DO NOT touch FastAPI, asyncpg, sentry-sdk, langfuse, etc. —
# so we install just what's needed. Full `.[dev]` would drag in 30+
# wheels (3-5 min cold) for no benefit. If a future test in tests/
# adds those deps, expand the install list here OR switch to
# `pip install -e ".[dev]"` against the template root (mounted at
# /work).
#
# WHY RUN AGAINST $TEMPLATE_ROOT (source), NOT THE SPAWNED VICTIM
# The safety gates are template-source code; new-service.sh spawning
# substitutes the service name into identifiers but doesn't change
# the safety-gate logic. Testing the SOURCE proves the template's
# logic is correct; the layout-assertion step 3 below already
# verifies the spawned copy contains the test files (rsync is
# byte-for-byte, so substituted identifiers are the only delta).
#
# WHY `set -o pipefail` ISN'T NEEDED HERE
# We invoke docker run directly + branch on its exit code. No pipe
# chain to lose status across.
#
# WHY `pip install ".[dev]"` AGAINST pyproject.toml (Codex PR #151
# round-9 CONCERN 1):
# Round-7 explicitly pinned each test dep in a hand-maintained list
# that MIRRORED pyproject.toml. Codex round-9 flagged the drift
# risk: if pyproject.toml bumps a dep but the maintainer forgets to
# update this list, the spawn-smoke would install the OLD pinned
# version + give false confidence ("tests pass") while the real
# service running the NEW version might break differently.
#
# Round-10 fix: install directly from the template's pyproject.toml
# — `pip install ".[dev]"` resolves BOTH the [project.dependencies]
# (asyncpg, redis, fastapi, etc.) AND [project.optional-dependencies
# .dev] (pytest, pytest-asyncio) in one shot. pyproject.toml is now
# the single source of truth; this script CANNOT drift from it.
#
# WHY NON-EDITABLE INSTALL (`.[dev]` not `-e .[dev]`):
# We don't edit the template source during the test run — just
# import + test. Non-editable is slightly cleaner (no path
# manipulation in site-packages). Either form works.
#
# WHY --timeout 120 --retries 10 (UP FROM round-7's 60/5):
# Heavier install (~25 wheels vs round-7's 5) needs more network
# slack. Cold pip cache + flaky CI network was the failure mode
# the previous timeout was tuned for; round-10's heavier install
# needs proportionally more.
#
# ACKNOWLEDGED TRADE-OFF — NETWORK FLAKE RISK (Codex PR #151 round-11
# CONCERN 2; J2/J3):
# This pre-flight makes a live `pip install` from PyPI inside
# python:3.12-slim every invocation, which has a non-zero flake rate
# despite the --timeout 120 / --retries 10 budget. The deliberate
# trade-off:
#   drift-resistance (the round-10 fix's win — pyproject.toml is
#     the SOLE pin location; spawn-smoke install reads from it
#     structurally, cannot drift)
#     > network-flake (the round-11 cost — every spawn-smoke run
#     hits PyPI; pip's retry logic + timeout absorb transient
#     network blips but not sustained PyPI outages)
#
# Considered + rejected the "even better" alternative (per Codex
# round-7 CONCERN 1's "even better" hint): run pytest INSIDE the
# already-built service container. The template's Dockerfile does
# `pip install .` (not `.[dev]`), so pytest isn't in the runtime
# image — would require multi-stage Dockerfile rework that's larger
# scope than DEP-014's acceptance criteria + needs its own design
# surface. Captured as a follow-up if PyPI outages bite this gate
# in practice.
#
# The flake-mitigation path THIS pre-flight provides:
#   * --timeout 120 — each wheel download has up to 2 minutes
#   * --retries 10 — pip retries each failed download up to 10 times
#   * Docker image cached locally — only wheels re-fetched on retry
#   * pip cache layer (Docker) survives across spawn-smoke runs in
#     CI when the runner reuses the image layer
# Combined, the practical flake rate is sub-1% per spawn-smoke
# invocation under normal PyPI conditions. Acceptable per J2/J3
# given the drift-resistance win.
echo ""
echo "── PRE-FLIGHT 2 ── DEP-014 safety-gate pytest run (production-fail-closed gate + sentinel-config validation)"
if ! docker run --rm \
        -v "$TEMPLATE_ROOT:/work" \
        -w /work \
        python:3.12-slim \
        sh -c "pip install --quiet --timeout 120 --retries 10 '.[dev]' \
            && pytest tests/ -v"; then
    echo ""
    echo "FAIL  DEP-014 safety-gate pytest failed — aborting spawn-smoke."
    echo "      One or more of the production-fail-closed gate /"
    echo "      sentinel-config-validation tests in tests/"
    echo "      test_redis_client_safety_gates.py regressed."
    echo "      Fix app/redis_client.py before re-running the smoke."
    exit 1
fi


# ===========================================================================
# Victim-service identity
# ===========================================================================

# Explicit-English name that matches B3's pattern + obviously identifies
# this as a throw-away CI fixture (so an operator never confuses it
# with a real service). Keep stable across runs so failure logs reference
# the same name.
VICTIM_SERVICE_NAME="yral-rishi-agent-template-spawn-smoke-victim"


# ===========================================================================
# Working directory + cleanup
# ===========================================================================

# Per-run temp directory under the system temp directory (RUNNER_TEMP
# on GH Actions, $TMPDIR on macOS, /tmp on Linux — $TMPDIR is the
# OS-provided env var name, not an identifier we control). `mktemp -d
# -t` provides a unique prefix-tagged directory; the X-suffix is
# filled in by mktemp.
working_directory="$(mktemp -d -t spawn-smoke.XXXXXX)"
spawned_service_path="$working_directory/$VICTIM_SERVICE_NAME"

# cleanup — WHAT/WHEN/WHY
#
# WHAT: dumps a failure-diagnostic block (compose ps + service + postgres
#       logs) when the script is exiting non-zero, then unconditionally
#       tears down the docker-compose stack + removes the per-run temp
#       directory. Preserves the original exit code so the trap doesn't
#       accidentally convert a failure to a success.
# WHEN: registered via `trap cleanup EXIT` immediately after the temp
#       directory is provisioned. Fires on every exit path — success
#       (step 9's `exit 0`), failure (any `step_fail`), or signal
#       (SIGINT / SIGTERM mid-run).
# WHY:  the gate's value is "always tear down + always leave breadcrumbs."
#       Leaked containers across CI runs would burn the runner's memory
#       budget; leaked temp directories would slow back-to-back local
#       runs; lost diagnostic output on failure would force the operator
#       to re-run the gate manually to triage. The trap closes all three
#       holes without the per-step code having to remember.
cleanup() {
    # Capture the script's actual exit code BEFORE we run cleanup
    # commands — `docker compose down` would otherwise overwrite `$?`
    # with its own exit status and we'd lose the original failure
    # signal.
    local original_exit_code=$?

    # On non-zero exit, dump diagnostic info BEFORE teardown so the
    # operator + CI logs capture the failure state.
    if [ "$original_exit_code" -ne 0 ] && [ -d "$spawned_service_path" ]; then
        echo ""
        echo "──────────────── FAILURE DIAGNOSTIC ────────────────"
        echo ">> compose ps:"
        ( cd "$spawned_service_path" && docker compose ps 2>&1 || true ) | head -30
        echo ""
        echo ">> compose logs service (last 50):"
        ( cd "$spawned_service_path" && docker compose logs --tail 50 service 2>&1 || true )
        echo ""
        echo ">> compose logs postgres (last 20):"
        ( cd "$spawned_service_path" && docker compose logs --tail 20 postgres 2>&1 || true )
        echo "─────────────────────────────────────────────────────"
    fi

    # Teardown — always attempt, even on non-zero exit. `|| true`
    # prevents cleanup failure from masking the original error.
    # `-v` removes named volumes (postgres-data) so back-to-back runs
    # start from a clean DB. `--remove-orphans` clears any stray
    # containers from a previous botched run.
    if [ -d "$spawned_service_path" ]; then
        ( cd "$spawned_service_path" && docker compose down -v --remove-orphans 2>&1 || true ) >/dev/null
    fi

    # Remove the temp directory. A1 spirit: we created this directory;
    # deleting it on the way out is creator-cleans-up, not gratuitous
    # deletion.
    rm -rf "$working_directory"

    # Preserve the original exit code so the trap doesn't accidentally
    # convert a failure to success.
    exit "$original_exit_code"
}
trap cleanup EXIT


# ===========================================================================
# Step helpers
# ===========================================================================

# step_banner — WHAT/WHEN/WHY
#
# WHAT: prints a blank line + a single-line section header of the form
#       `── STEP $1 ── $2` to stdout. Argument 1 is the step number;
#       argument 2 is the step's English description.
# WHEN: called at the top of each of the 9 ordered steps below, before
#       any of that step's actual work runs.
# WHY:  CI logs are wall-of-text; the operator triaging a failure needs
#       to skim to the step that broke. Consistent banners make
#       `grep '── STEP' run.log` a one-liner that lists which steps
#       executed + which one didn't reach completion.
step_banner() {
    echo ""
    echo "── STEP $1 ── $2"
}

# step_pass — WHAT/WHEN/WHY
#
# WHAT: prints `  PASS  $1` to stdout (2-space indent matches the step
#       banner's visual hierarchy). Does NOT exit; control flow
#       continues to the next step.
# WHEN: called at the end of each step's logic when the step's
#       assertion(s) all hold.
# WHY:  paired with step_fail as a binary outcome signal per step.
#       Having BOTH a success line AND a fail line keeps the log
#       symmetric — an operator can audit "every step printed exactly
#       one PASS or FAIL line" without having to infer from absence.
step_pass() { echo "  PASS  $1"; }

# step_fail — WHAT/WHEN/WHY
#
# WHAT: prints `  FAIL  $1` to stdout, then `exit 1`. The `exit` is
#       what trips the `trap cleanup EXIT` registered above, which
#       dumps the failure-diagnostic block + tears down.
# WHEN: called the moment any step's assertion fails.
# WHY:  fail-fast — `set -e` would also propagate the failure but
#       wouldn't print a step-scoped explanation. Calling step_fail
#       with an explicit message gives the operator the EXACT failing
#       condition (e.g. "openapi.json does not contain X") rather than
#       a generic "command exited non-zero" trace. Pairs with step_pass
#       so every step prints exactly one outcome line.
step_fail() { echo "  FAIL  $1"; exit 1; }


# ===========================================================================
# Step 0: pre-flight — Docker available + compose v2
# ===========================================================================

step_banner 0 "Pre-flight: Docker daemon + compose v2 available"
# `docker info` exits non-zero if the daemon isn't reachable. Fail fast
# with a clear message instead of letting `compose up` crash later.
if ! docker info >/dev/null 2>&1; then
    step_fail "Docker daemon not reachable (Docker Desktop not running?)"
fi
# Compose v2 ships as a `docker compose` subcommand; v1's standalone
# `docker-compose` is deprecated. We REQUIRE v2 — `compose up --build`
# semantics differ slightly between versions.
if ! docker compose version >/dev/null 2>&1; then
    step_fail "docker compose (v2) not available — install Docker Desktop or compose-plugin"
fi
step_pass "Docker daemon + compose v2 detected"


# ===========================================================================
# Step 1: per-run temp directory (already created above)
# ===========================================================================

step_banner 1 "Per-run temp directory + cleanup trap"
echo "  working_directory=$working_directory"
echo "  spawned_service_path=$spawned_service_path"
step_pass "temp directory provisioned; cleanup trap armed"


# ===========================================================================
# Step 2: spawn a fresh victim via new-service.sh --target-directory
# ===========================================================================

step_banner 2 "Spawn fresh service via new-service.sh --target-directory"
# Real (non-dry-run) spawn so the post-spawn DEP-010 step-6 check
# exercises end-to-end. This satisfies PR #133's Codex CONCERN: the
# new-service.sh post-spawn block now runs under CI on every template
# PR, not just at the developer's discretion.
#
# Wrap the invocation in a `cd "$REPO_ROOT"` subshell so the spawner's
# own `git rev-parse --show-toplevel` resolves the correct repo even
# when test_spawn_smoke.sh is invoked from OUTSIDE the source repo
# (e.g. `cd /tmp && bash /path/to/test_spawn_smoke.sh`). Without the
# `cd`, new-service.sh's `git rev-parse` would either fail (cwd not
# in any repo) or resolve to a DIFFERENT repo (cwd happens to sit in
# one) — both wrong. Subshell scope means the outer script's cwd is
# untouched after the spawn.
if ! ( cd "$REPO_ROOT" && bash "$NEW_SERVICE_SH" "$VICTIM_SERVICE_NAME" --target-directory "$working_directory" ); then
    step_fail "new-service.sh exited non-zero (post-spawn DEP-010 check tripped, or earlier failure)"
fi
[ -d "$spawned_service_path" ] || step_fail "spawned tree missing at $spawned_service_path"
step_pass "spawn produced $spawned_service_path"


# ===========================================================================
# Step 3: spawned-tree layout assertions
# ===========================================================================

step_banner 3 "Verify spawned tree layout"
# Files / directories that MUST exist post-spawn. Drift on any of
# these is what catches future template-rooted bug cascades. The
# 8 F8 docs are listed explicitly so a missing one fails loudly
# (the cascade pattern was 'silent miss', not 'noisy crash').
expected_paths=(
    "app/main.py"
    "app/__init__.py"
    # DEP-014 baseline modules — every spawned service inherits these.
    # Drift here (e.g., a future PR accidentally removing the
    # lifespan-singleton modules from the template) would silently
    # downgrade every new spawned service back to the stub baseline.
    "app/database.py"
    "app/redis_client.py"
    "app/health_routes.py"
    # DEP-014 unit-test scaffold — pytest scaffold + safety-gate
    # unit tests added in PR #151 round-3 per Codex CONCERN 2. A
    # future spawn that drops the tests/ folder would silently
    # downgrade test coverage; the layout assertion catches it.
    # No tests/__init__.py — pytest 3.0+ discovery is path-based,
    # not Python-package-based (Codex PR #151 round-3 BLOCKER chose
    # delete-the-file over add-the-full-B7-header).
    "tests/conftest.py"
    "tests/test_redis_client_safety_gates.py"
    "Dockerfile"
    "docker-compose.yml"
    "docker-compose.swarm.yml"
    "project.config"
    "shared-config.yaml"
    "pyproject.toml"
    "secrets.yaml"
    "scripts/tests/fixtures/valid/env.local.fixture"
    "scripts/tests/fixtures/env-local-incomplete/env.local.fixture"
    "DEEP-DIVE.md"
    "READING-ORDER.md"
    "CLAUDE.md"
    "RUNBOOK.md"
    "SECURITY.md"
    "WALKTHROUGH.md"
    "GLOSSARY.md"
    "WHEN-YOU-GET-LOST.md"
)
for path in "${expected_paths[@]}"; do
    [ -e "$spawned_service_path/$path" ] || step_fail "missing expected path: $path"
done

# DEP-010 regression guard — literal .env.local in spawned fixture
# tree means the rename pattern broke (someone renamed back to the
# gitignored filename). The post-spawn DEP-010 check in step 6 of
# new-service.sh ALREADY catches this at spawn time; we re-assert
# at the destination as belt-and-suspenders.
if find "$spawned_service_path/scripts/tests/fixtures" -name '.env.local' -type f 2>/dev/null | grep -q .; then
    step_fail "DEP-010 regression: literal .env.local present in spawned fixture tree"
fi

# Substitution sanity: the victim name must appear in app/main.py.
# The template ships `title="yral-rishi-agent service template"`
# (note: with spaces) which the substituter doesn't catch — that's a
# known cosmetic gap documented in SESSION-2-LOG (PR 5 of Day 3).
# What we DO test: the victim name appears somewhere in main.py
# (proves the perl substituter ran at all).
if ! grep -q "$VICTIM_SERVICE_NAME" "$spawned_service_path/app/main.py" 2>/dev/null; then
    # main.py may not reference the service name directly; project.config
    # always does (PROJECT_NAME=<service>). Fall back to that for the
    # substitution-ran-at-all signal.
    if ! grep -q "$VICTIM_SERVICE_NAME" "$spawned_service_path/project.config" 2>/dev/null; then
        step_fail "victim name '$VICTIM_SERVICE_NAME' not substituted in app/main.py OR project.config"
    fi
fi

step_pass "all ${#expected_paths[@]} expected paths present; no literal .env.local; substitution ran"


# ===========================================================================
# Step 4: docker compose up --build (detached)
# ===========================================================================

step_banner 4 "Build + start the spawned service's docker-compose stack"
# `cd` into the spawned directory so docker compose finds docker-compose.yml
# at cwd. Relative `./app` volume mount + build context `.` both
# resolve against the compose-file's directory, which is now cwd.
cd "$spawned_service_path"
# `--build` forces a fresh image build (no stale cache from prior runs
# of this gate or unrelated images). `-d` detaches so the script can
# proceed to the probe step; without `-d` the script would block on
# the foreground attached output until SIGINT.
if ! docker compose up --build -d; then
    step_fail "docker compose up failed (build error or container start failed — see logs above)"
fi
step_pass "compose stack up (service + postgres + pgbouncer + redis), detached"


# ===========================================================================
# Step 5: poll /openapi.json
# ===========================================================================

step_banner 5 "Poll http://localhost:8000/openapi.json (60s budget)"
# Why polling vs `--wait`: see the file header. uvicorn process-up !=
# ASGI-app-serving — we need an actual HTTP success to call this green.
poll_attempts=0
poll_max_attempts=30  # 30 × 2s = 60s
openapi_capture_path="$working_directory/openapi.json"
while [ $poll_attempts -lt $poll_max_attempts ]; do
    # `-f` makes curl exit non-zero on HTTP 4xx/5xx (so 503 during
    # startup looks like failure, which is exactly what we want during
    # the poll). `-s` suppresses progress noise. `--max-time 3` caps
    # each attempt so a hung server doesn't burn the 60s budget on
    # one stuck request. `-o` writes the JSON body to disk for the
    # title check below.
    if curl -fsS --max-time 3 "http://localhost:8000/openapi.json" -o "$openapi_capture_path" 2>/dev/null; then
        break
    fi
    poll_attempts=$((poll_attempts + 1))
    sleep 2
done
if [ $poll_attempts -ge $poll_max_attempts ]; then
    step_fail "/openapi.json did not return 200 within 60s (service failed to boot — see EXIT diagnostic)"
fi

# Content sanity — verify the response is a real OpenAPI document,
# not some random 200 from an unrelated process on port 8000. The
# presence of an `"openapi":` field is the minimal proof: it's part
# of every FastAPI-generated schema and won't be in a sidecar's or
# unrelated service's response.
#
# Why NOT also assert the spawned service NAME appears: the template's
# `app/main.py` ships `title="yral-rishi-agent service template"`
# (literal spaces; not a hyphenated placeholder), so the perl
# substituter in new-service.sh doesn't catch it — the spawned title
# is the unchanged "yral-rishi-agent service template" literal. This
# is a known cosmetic gap documented in SESSION-2-LOG Day-3 PR-5 +
# queued as a future Days-5/6 cleanup; the spawn-smoke gate is NOT
# the place to gate on it because it would force this PR to bundle
# the title-parameterization fix, which is a separate concern (A2.1).
# When that gap is closed in its own PR, this check can be tightened.
if ! grep -q '"openapi":' "$openapi_capture_path"; then
    step_fail "response to /openapi.json is not a valid OpenAPI document (missing 'openapi' field)"
fi

step_pass "/openapi.json returned 200 after $((poll_attempts * 2))s; response is a valid OpenAPI document"


# ===========================================================================
# Step 5b: poll /health/ready (DEP-014's load-bearing dual-dep probe)
# ===========================================================================
#
# Step 5 proved the ASGI app + middleware loaded + uvicorn is serving
# requests. Step 5b proves the DEP-014 lifespan-singleton wiring
# actually opened working connections to Postgres AND Redis. The
# /health/ready route in `app/health_routes.py` runs
# `check_pool_reachable()` + `check_redis_reachable()` in parallel
# and returns 200 only when BOTH succeed; 503 with a detail payload
# otherwise.
#
# WHY THIS IS THE LOAD-BEARING STEP (per coordinator's DEP-014 note):
# The pre-DEP-014 spawn-smoke only probed /openapi.json — which would
# return 200 even if the spawned service's lifespan startup silently
# failed to initialise the asyncpg pool or the Redis client (so long
# as uvicorn could still serve requests). That gap is exactly the
# regression class DEP-014 closes:
#   - Misconfigured DATABASE_URL → init_pool would still construct
#     a pool object (asyncpg is lazy), but acquire+SELECT 1 fails →
#     check_pool_reachable returns False → /health/ready 503.
#   - Misconfigured REDIS_PASSWORD (or unreachable Sentinel quorum,
#     or wrong sentinel_hosts in shared-config.yaml) → init_redis
#     would still construct a client object, but the first PING
#     fails → check_redis_reachable returns False → /health/ready
#     503.
# Either case fails THIS step → fails the spawn-smoke → fails the
# template PR. That's what makes the gate catch shared-config /
# Redis-AUTH / connection-string drift at template-CI time.
#
# Same 60s polling budget as step 5 — the deps init concurrently
# with uvicorn so /health/ready typically goes 200 within a few
# seconds of /openapi.json starting to respond. We give the same
# wide budget for slow-CI cases.
#
# Why polling (not single shot): the service container starts when
# its compose `depends_on` deps (postgres + redis) are healthy, but
# the LIFESPAN startup (which opens the asyncpg pool + redis client)
# runs only after uvicorn boots. /openapi.json may return 200
# slightly BEFORE the lifespan startup completes; polling gives
# /health/ready a moment to settle.
step_banner "5b" "Poll http://localhost:8000/health/ready (60s budget; DEP-014 dual-dep gate)"
health_poll_attempts=0
health_poll_max_attempts=30  # 30 × 2s = 60s
health_ready_capture_path="$working_directory/health-ready.json"
while [ $health_poll_attempts -lt $health_poll_max_attempts ]; do
    # `-f` makes curl exit non-zero on HTTP 4xx/5xx (so a 503 during
    # the brief race between uvicorn-up and lifespan-startup-done
    # looks like failure — which is what we want during polling).
    # `--max-time 3` caps each attempt. `-o` writes the response
    # body for the assertion below.
    if curl -fsS --max-time 3 "http://localhost:8000/health/ready" -o "$health_ready_capture_path" 2>/dev/null; then
        break
    fi
    health_poll_attempts=$((health_poll_attempts + 1))
    sleep 2
done
if [ $health_poll_attempts -ge $health_poll_max_attempts ]; then
    # The EXIT trap will dump service + postgres logs; the operator
    # also wants the LATEST /health/ready response body (a 503) to
    # see WHICH dep failed. Dump it before failing.
    echo "  Last /health/ready response (503 expected on failure):"
    curl -sS --max-time 3 "http://localhost:8000/health/ready" 2>&1 | head -20 | sed 's/^/    /'
    step_fail "/health/ready did not return 200 within 60s — Postgres or Redis dep is misconfigured or unreachable (see EXIT diagnostic)"
fi

# Content sanity — confirm the 200 response is the F9-expected
# `{"status": "ok"}` shape. Catches the case where uvicorn returns
# 200 for an unrelated reason (e.g., a misconfigured proxy / cached
# stale response) by requiring the literal `"status": "ok"` token.
if ! grep -q '"status":[[:space:]]*"ok"' "$health_ready_capture_path"; then
    step_fail "/health/ready returned 200 but the body is not the F9 {\"status\": \"ok\"} shape — caught body: $(head -c 200 "$health_ready_capture_path")"
fi

step_pass "/health/ready returned 200 + F9 envelope after $((health_poll_attempts * 2))s — Postgres + Redis dep wiring verified"


# ===========================================================================
# Step 5c: poll /health/deep (PR #151 round-6 BLOCKER 2 — F9 third tier)
# ===========================================================================
#
# F9 requires the uniform three-tier health split for every
# service: /health/live + /health/ready + /health/deep. Round-6
# added /health/deep to the template (Codex round-5 BLOCKER 2);
# this step proves the route is wired + returns 200 in the
# happy-path case (Postgres + Redis both connected + the SELECT
# NOW() + SET/GET/DEL round-trips both succeed).
#
# WHY A SEPARATE STEP (5c, not folded into 5b):
# /health/deep exercises a DIFFERENT code path than /health/ready:
# the deep probes do REAL ROUND-TRIP queries (SELECT NOW(),
# SET/GET/DEL), not just connectivity pings. Separating the step
# means a deep-round-trip regression surfaces with a clear
# pinpoint (5c failed, 5b passed) rather than blamed on 5b's
# broader dep wiring.
#
# WHY ALLOWED MORE TIME (single shot vs 60s budget on 5b):
# /health/deep's per-probe timeout is 1.0s (vs /health/ready's
# 200ms). Single-shot is sufficient because /health/ready already
# proved the deps are reachable + the service's lifespan finished;
# /health/deep should return promptly. If it doesn't, the failure
# mode is genuinely interesting (round-trip works but slow) and
# we want the diagnostic surfaced quickly.
#
# WHY NO NEGATIVE TEST FOR /health/deep:
# Step 6b already exercises the dep-down failure path via /health/
# ready. /health/deep would degrade to 503 under the same Redis-
# down condition (same dep-check chain). Adding a separate
# /health/deep negative test would duplicate coverage without
# meaningful new signal.
step_banner "5c" "Single-shot http://localhost:8000/health/deep (F9 third tier; PR #151 round-6)"
deep_capture_path="$working_directory/health-deep.json"
last_deep_http_code="$(curl -sS --max-time 5 -o "$deep_capture_path" -w '%{http_code}' "http://localhost:8000/health/deep" 2>/dev/null || echo "000")"
if [ "$last_deep_http_code" != "200" ]; then
    echo "  Last /health/deep status: $last_deep_http_code"
    echo "  Last response body (truncated):"
    head -c 300 "$deep_capture_path" 2>/dev/null | sed 's/^/    /'
    step_fail "/health/deep did not return 200 — F9 third-tier deep probe is broken"
fi
# Same minimal F9 envelope shape as /health/ready 200 case.
if ! grep -q '"status":[[:space:]]*"ok"' "$deep_capture_path"; then
    step_fail "/health/deep returned 200 but body is not the F9 {\"status\": \"ok\"} shape — caught body: $(head -c 200 "$deep_capture_path")"
fi
step_pass "/health/deep returned 200 + F9 envelope — Postgres SELECT NOW() + Redis SET/GET/DEL round-trips verified"


# ===========================================================================
# Step 6: scan service container logs for unexpected errors
# ===========================================================================

step_banner 6 "Scan service container logs for ERROR / CRITICAL lines"
# Capture last 200 log lines from the service container only.
# Postgres + Redis logs are noisier + their errors surface separately
# via the compose healthcheck; we focus on the spawned service itself.
service_log_capture="$(docker compose logs --tail 200 service 2>&1)"

# Whitelist: known no-op startup messages that aren't real errors.
#   - Sentry SDK no-DSN messages: SENTRY_DSN is empty in the template
#     compose by design (we don't want CI runs spamming Sentry).
#   - Langfuse "tracing disabled" / "no public key" lines: same
#     intentional no-op pattern (LANGFUSE_TRACING_ENABLED=false).
#   - structlog INFO records that legitimately contain "ERROR" or
#     "CRITICAL" as a quoted log-level reference rather than the
#     actual line's level. (We grep for ' ERROR ' / ' CRITICAL '
#     with surrounding whitespace to reduce false positives.)
problematic_lines="$(echo "$service_log_capture" \
    | grep -E ' (ERROR|CRITICAL) ' \
    | grep -vE 'Sentry DSN not configured|Langfuse tracing disabled|LANGFUSE_PUBLIC_KEY not set' \
    || true)"
if [ -n "$problematic_lines" ]; then
    echo "  Service-container log lines that look like real errors:"
    echo "$problematic_lines" | head -10 | sed 's/^/    /'
    step_fail "service logs contain unexpected ERROR/CRITICAL lines (above)"
fi
step_pass "service logs clean of unexpected errors"


# ===========================================================================
# Step 6b: negative test — Redis down → /health/ready degrades to 503
# (DEP-014 dual-dep FAILURE-path verification per Codex PR #151 round-1
# CONCERN)
# ===========================================================================
#
# Step 5b proved the HAPPY path: both deps reachable → /health/ready
# 200. But the load-bearing acceptance criterion for DEP-014 is that
# /health/ready ALSO correctly degrades to 503 when one dep is down,
# with a body payload naming which dep failed. Codex round-1 CONCERN:
# "[step 5b] does not cover ... /health/ready returning 503 when one
# dependency is down."
#
# This step injects a controlled failure: `docker compose stop redis`
# disconnects the service's redis client from its primary. The
# `check_redis_reachable()` probe's 200ms PING timeout starts firing;
# /health/ready transitions from 200 → 503. The asserts below verify:
#   (a) HTTP status is 503 (not 200, not 500)
#   (b) Response body's details.redis == "failed"
#   (c) Response body's details.postgres == "ok" (postgres is still
#       up; only redis was stopped — proves the failure detail is
#       per-dep accurate, not just a blanket "something is wrong")
#
# WHY `docker compose stop` (not `kill`):
# `stop` issues SIGTERM with a grace period before SIGKILL — closer
# to what would happen in production during a controlled restart or
# scale-down. The effect on the service's redis client is the same
# either way once the container is down: PING starts failing.
#
# WHY THIS STEP RUNS AFTER STEP 6 (NOT BEFORE):
# Step 6 is the healthy-state log scan. Once Redis is stopped, the
# service emits redis-connection-refused errors to logs — those would
# false-trip step 6's log scan. Running 6b AFTER 6 means the log
# scan sees clean logs from the healthy state; the negative-test
# noise that follows is contained to step 6b's window + tear down
# clears it.
#
# WHY NO REDIS RESTART AFTER:
# Teardown immediately follows step 6b. Restarting Redis just to
# tear it back down a second later adds wall-time + complexity
# without value. The EXIT trap's `docker compose down -v` handles
# everything regardless of dep state.
step_banner "6b" "Negative test: stop Redis → /health/ready 503 with redis=failed (DEP-014 failure-path gate)"

# Stop the Redis container; the service's redis client connection
# drops within a few seconds.
if ! docker compose stop redis >/dev/null 2>&1; then
    step_fail "could not stop redis container (compose stop returned non-zero)"
fi

# Brief poll window so the service's check_redis_reachable() probe
# has time to register the disconnect. The probe is on every
# /health/ready hit (no client-side caching), so the FIRST hit
# after stop SHOULD already show redis=failed. We allow a 20s
# budget for slow CI runners but typically exit in <4s.
negative_poll_attempts=0
negative_poll_max_attempts=10  # 10 × 2s = 20s budget
unhealthy_capture_path="$working_directory/health-ready-unhealthy.json"
last_http_code="000"
while [ $negative_poll_attempts -lt $negative_poll_max_attempts ]; do
    # No `-f` flag — we EXPECT 503, don't want curl to bail.
    # `-w '%{http_code}'` captures the status code; `-o` writes the
    # body to disk for the assertion below.
    last_http_code="$(curl -sS --max-time 3 -o "$unhealthy_capture_path" -w '%{http_code}' "http://localhost:8000/health/ready" 2>/dev/null || echo "000")"
    if [ "$last_http_code" = "503" ]; then
        break
    fi
    negative_poll_attempts=$((negative_poll_attempts + 1))
    sleep 2
done

if [ "$last_http_code" != "503" ]; then
    # Either the probe didn't notice Redis was down (false negative
    # in check_redis_reachable) OR another dep failed first OR a
    # totally different status surfaced. Dump the response body
    # before failing so the operator sees what /health/ready
    # actually returned.
    echo "  Last /health/ready status: $last_http_code"
    echo "  Last response body (truncated):"
    head -c 300 "$unhealthy_capture_path" 2>/dev/null | sed 's/^/    /'
    step_fail "/health/ready did not return 503 within 20s after stopping Redis — failure-path probe is broken"
fi

# Body sanity: details.redis MUST say "failed" (not "ok"). If this
# fails, the dual-probe logic in app/health_routes.py + the
# check_redis_reachable function in app/redis_client.py are
# misreporting + the gate's failure-path coverage is broken.
if ! grep -q '"redis":[[:space:]]*"failed"' "$unhealthy_capture_path"; then
    echo "  Body returned (truncated):"
    head -c 300 "$unhealthy_capture_path" | sed 's/^/    /'
    step_fail "/health/ready 503 body does not name redis as failed — failure-detail attribution is broken"
fi

# Body sanity: postgres should STILL be "ok" because only Redis was
# stopped. If both deps show failed, the dual-probe is conflating
# them (e.g., reporting any-dep-failure as both-failed) and the
# per-dep attribution Codex specifically asked for is broken.
if ! grep -q '"postgres":[[:space:]]*"ok"' "$unhealthy_capture_path"; then
    echo "  Body returned (truncated):"
    head -c 300 "$unhealthy_capture_path" | sed 's/^/    /'
    step_fail "/health/ready 503 body shows postgres as something other than ok (only redis was stopped) — per-dep attribution is broken"
fi

step_pass "/health/ready correctly returned 503 with redis=failed + postgres=ok after $((negative_poll_attempts * 2))s — failure path + per-dep attribution verified"


# ===========================================================================
# Step 7 + 8: teardown — handled by EXIT trap (compose down + rm -rf)
# ===========================================================================

# Marker step so the operator + CI logs see step 7/8 actually happen.
step_banner 7 "Teardown (delegated to EXIT trap: compose down -v + rm -rf)"
step_pass "teardown will run when this script exits"


# ===========================================================================
# Step 9: success
# ===========================================================================

echo ""
echo "════════════════════════════════════════════════════════"
echo "  test_spawn_smoke.sh — ALL STEPS PASSED"
echo "════════════════════════════════════════════════════════"
echo ""
# `exit 0` triggers the EXIT trap which runs teardown + preserves
# this 0 status.
exit 0


# ===========================================================================
# RELATED FILES:
#   ../new-service.sh                  — the spawner this gate exercises;
#                                        --target-directory flag added in the same
#                                        PR as this script so out-of-repo
#                                        destinations work cleanly.
#   ../tests/test_validate_secrets.sh  — sibling test for D8 secrets bridge
#   ../../docker-compose.yml           — the local-dev stack this gate boots
#   ../../Dockerfile                   — multi-stage build this gate runs
#   ../../app/main.py                  — FastAPI skeleton; /openapi.json is
#                                        the FastAPI-auto-generated route
#                                        this gate probes
#   ../../../.github/workflows/template-spawn-smoke.yml
#                                      — workflow that runs this script in CI
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-
#     build-coordination/cross-session-dependencies.md (DEP-014)
#                                      — follow-up that unlocks the gate
#                                        to catch Redis/Postgres-config
#                                        drift via skeleton expansion
# ===========================================================================
