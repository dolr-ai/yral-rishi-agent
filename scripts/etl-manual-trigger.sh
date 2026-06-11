#!/bin/bash
# etl-manual-trigger.sh — Piece A of the on-demand ETL drain system.
#
# What this does:
#   Runs scripts/incremental_export.py --force RIGHT NOW instead of
#   waiting up to 5 min for the cron tick. Used by the
#   etl-drain.yml GitHub Actions workflow via SSH.
#
# What this is NOT:
#   A replacement for the cron job. The 5-min cron stays exactly as is.
#   This script is the manual-trigger path on top of it.
#
# Concurrency model:
#   flock on /tmp/etl-export-manual.lock guarantees TWO MANUAL runs
#   serialize. If a cron tick happens during a manual run, both writers
#   will append to state.json — state.json is read-modify-write per
#   table, and the LAST writer wins. The bounded risk is one duplicate
#   tick's worth of data in S3, which the V2 importer dedupes via
#   etl_processed_files (file PK).
#
#   Long-term, if cron + manual collisions become a real problem, both
#   sides should use the same flock. Today the cron-side script doesn't
#   take a lock, so we only protect from manual-vs-manual races.
#
# Exit codes:
#   0   — export ticked, integrity layers emitted, S3 files uploaded
#   78  — config refusal (lock file unwritable, script not installed)
#         — sysexits.h EX_CONFIG; same convention as run-migrations.sh.
#   *   — propagated from incremental_export.py (1 on failure)
#
# Install location on rishi-1: ~/.etl-export/etl-manual-trigger.sh
# Invocation from the workflow:  ssh rishi-1 'bash ~/.etl-export/etl-manual-trigger.sh'

set -euo pipefail

LOCK_FILE="/tmp/etl-export-manual.lock"
LOG_FILE="/tmp/etl-export-manual.log"
SCRIPT_DEFAULT="${HOME}/.etl-export/incremental_export.py"
SCRIPT_PATH="${ETL_EXPORT_SCRIPT:-$SCRIPT_DEFAULT}"

if [ ! -f "$SCRIPT_PATH" ]; then
    echo "[etl-manual-trigger] FATAL: exporter script not at $SCRIPT_PATH" >&2
    echo "[etl-manual-trigger] Install path is configurable via ETL_EXPORT_SCRIPT env var." >&2
    exit 78
fi

# Non-blocking lock: if another manual run is already in progress,
# refuse rather than queue. The workflow polls; queueing here would
# silently extend the wall clock.
exec 9>"$LOCK_FILE" || {
    echo "[etl-manual-trigger] FATAL: could not open lock file $LOCK_FILE" >&2
    exit 78
}
if ! flock -n 9; then
    echo "[etl-manual-trigger] another manual trigger is in progress — refusing to start a parallel run" >&2
    exit 78
fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[etl-manual-trigger] $ts — starting export with --force" | tee -a "$LOG_FILE"

# Run the export. Output goes to both stdout (so SSH stream shows it
# in the workflow log) and the on-host log file (so a future operator
# can see history without re-running the workflow).
if python3 "$SCRIPT_PATH" --force 2>&1 | tee -a "$LOG_FILE"; then
    RC=0
else
    # `set -o pipefail` makes this capture the exporter's exit code
    # rather than tee's. PIPESTATUS gives the same answer; using
    # PIPESTATUS to be explicit.
    RC=${PIPESTATUS[0]}
fi

ts2=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[etl-manual-trigger] $ts2 — export exited with rc=$RC" | tee -a "$LOG_FILE"
exit "$RC"
