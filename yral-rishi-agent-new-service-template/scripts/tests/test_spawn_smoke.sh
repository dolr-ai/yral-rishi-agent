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
# A single EXIT trap guarantees `compose down -v` + tempdir removal even
# on signal / abort.
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
#   - Local mac: `bash yral-rishi-agent-new-service-template/scripts/
#     tests/test_spawn_smoke.sh` (Docker Desktop must be running).
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
# (CI does `bash <full-path>`; operator may run from any folder).
TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
TEMPLATE_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
NEW_SERVICE_SH="$SCRIPTS_DIR/new-service.sh"


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

# Per-run temp dir under the system tempdir (RUNNER_TEMP on GH Actions,
# $TMPDIR on macOS, /tmp on Linux). `mktemp -d -t` provides a unique
# prefix-tagged dir; the X-suffix is filled in by mktemp.
working_directory="$(mktemp -d -t spawn-smoke.XXXXXX)"
spawned_service_path="$working_directory/$VICTIM_SERVICE_NAME"

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

    # Remove the temp dir. A1 spirit: we created this dir; deleting
    # it on the way out is creator-cleans-up, not gratuitous deletion.
    rm -rf "$working_directory"

    # Preserve the original exit code so the trap doesn't accidentally
    # convert a failure to success.
    exit "$original_exit_code"
}
trap cleanup EXIT


# ===========================================================================
# Step helpers
# ===========================================================================

# Banner for each step — makes CI logs scannable.
step_banner() {
    echo ""
    echo "── STEP $1 ── $2"
}

# Step-result helpers. PASS prints + continues; FAIL prints + exits 1
# which trips the EXIT trap's diagnostic dump.
step_pass() { echo "  PASS  $1"; }
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
# Step 1: per-run temp dir (already created above)
# ===========================================================================

step_banner 1 "Per-run temp dir + cleanup trap"
echo "  working_directory=$working_directory"
echo "  spawned_service_path=$spawned_service_path"
step_pass "tempdir provisioned; cleanup trap armed"


# ===========================================================================
# Step 2: spawn a fresh victim via new-service.sh --target-dir
# ===========================================================================

step_banner 2 "Spawn fresh service via new-service.sh --target-dir"
# Real (non-dry-run) spawn so the post-spawn DEP-010 step-6 check
# exercises end-to-end. This satisfies PR #133's Codex CONCERN: the
# new-service.sh post-spawn block now runs under CI on every template
# PR, not just at the developer's discretion.
if ! bash "$NEW_SERVICE_SH" "$VICTIM_SERVICE_NAME" --target-dir "$working_directory"; then
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
# `cd` into the spawned dir so docker compose finds docker-compose.yml
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
#                                        --target-dir flag added in the same
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
