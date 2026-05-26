# Sentry Baseline Pull — Daily Cron

This folder holds the Day-0.5 deliverable for Session 1 (Infra & Cluster):
the script + macOS launchd schedule + secrets manifest that pulls
yral-chat-ai latency from `sentry.rishi.yral.com` once a day. The output
is the moving target that CONSTRAINTS row **E1** says v2 must beat by 50%.

## Files

| File | What it is |
|---|---|
| `pull-sentry-baseline.py` | The Python script that calls the Sentry Discover API and appends to `../daily-baseline.csv` + rewrites `../latest-baseline.md`. Stdlib-only. |
| `pull-sentry-baseline.plist.template` | launchd schedule template (placeholders `__YRAL_REPO_ROOT__` and `__USER_HOME__`). |
| `install-launchd-job.sh` | One-time installer — renders the template into `~/Library/LaunchAgents/` and bootstraps it into launchd. |
| `secrets.yaml` | Declarative secrets manifest (per CONSTRAINTS D7+D8) for the Sentry auth token this folder needs. |
| `README.md` | This file. |

## First-time install (run once)

The script reads the Sentry auth token from macOS Keychain. Add it once,
then install the launchd job. The token comes from
`https://sentry.rishi.yral.com` (Settings → Account → API → Auth Tokens —
`event:read` + `project:read` scopes are sufficient).

```bash
# 1. Store the token in Keychain (replace <paste-your-token>):
security add-generic-password -U \
    -a dolr-ai \
    -s SENTRY_AUTH_TOKEN \
    -w '<paste-your-token>' \
    -T /usr/bin/security

# 2. Verify the entry exists:
security find-generic-password -a dolr-ai -s SENTRY_AUTH_TOKEN -w
# (should print the token)

# 3. Install the daily 9:00 a.m. launchd job:
cd '/Users/rishichadha/Claude Projects/yral-rishi-agent'
bash yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/install-launchd-job.sh

# 4. Trigger one immediate run to confirm it works end-to-end
#    (does NOT replace the daily schedule):
launchctl kickstart -k gui/$(id -u)/com.dolr-ai.yral-rishi-agent.pull-sentry-baseline

# 5. Inspect the output:
tail -n 100 ~/.local/share/yral-rishi-agent/baseline-cron.stdout.log
tail -n 100 ~/.local/share/yral-rishi-agent/baseline-cron.stderr.log
cat yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/latest-baseline.md
```

After step 5, the job runs every morning at **9:00 a.m. local time** (which
is 9:00 IST when the laptop's timezone is `Asia/Kolkata`). If the laptop is
asleep, launchd queues the run and fires it as soon as the laptop wakes.

## What the script writes

Two files in the parent folder
`yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/`:

- **`daily-baseline.csv`** — append-only history. One row per (date,
  Sentry transaction). Columns: `pull_date_utc`, `sentry_organization_slug`,
  `sentry_project_slug`, `transaction_name`, `request_count`,
  `p50_milliseconds`, `p95_milliseconds`, `p99_milliseconds`,
  `failure_rate`, `lookback_period`. Future v2 CI latency gate reads this
  file to compute the **0.5× target** every PR must beat.
- **`latest-baseline.md`** — overwritten on every run. A markdown summary
  of the freshest snapshot for at-a-glance reading by Rishi or the
  coordinator session.

## Configuration

The script defaults match the build plan:
`https://sentry.rishi.yral.com`, organization `dolr-ai`,
project `yral-chat-ai`, lookback `24h`. To override either slug without
changing code, uncomment the relevant line in
`pull-sentry-baseline.plist.template` under `EnvironmentVariables`,
re-run `install-launchd-job.sh`, and the next 9 a.m. trigger picks them up.

The Sentry **host** is hardcoded per CONSTRAINTS A7 — never override it.

## Verify it ran

```bash
# Did launchd kick off the latest scheduled run?
launchctl print gui/$(id -u)/com.dolr-ai.yral-rishi-agent.pull-sentry-baseline | head

# Did the script succeed?
tail -n 5 ~/.local/share/yral-rishi-agent/baseline-cron.stdout.log
# Expect a line like:
# OK pull-sentry-baseline 2026-05-04 organization=dolr-ai project=yral-chat-ai rows=18

# Anything fail?
tail -n 50 ~/.local/share/yral-rishi-agent/baseline-cron.stderr.log
# Empty file or just startup banners → all good.
# Lines starting with "ERROR pull-sentry-baseline:" → see "Troubleshooting" below.
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Keychain entry missing` in stderr log | Token never added, or added under a different account/service name | Re-run step 1 of install |
| `Reading Keychain timed out` | Login keychain is locked (rare on macOS desktop sessions) | Unlock with `security unlock-keychain login.keychain-db` |
| `Sentry returned HTTP 401` | Token is wrong, expired, or rotated | Generate a new token at sentry.rishi.yral.com, replace in Keychain (`security add-generic-password -U …`) |
| `Sentry returned HTTP 403` | Token lacks `event:read` or `project:read` | Recreate the token with both scopes ticked |
| `Sentry returned 0 transactions for organization=… project=…` | Slug mismatch, OR the project genuinely had zero traffic in the last 24h | Verify the slug in the Sentry UI; if traffic is real, set `SENTRY_PROJECT_SLUG` env override in the plist |
| Job never fires at 9 a.m. | Laptop is permanently asleep, OR launchd job not loaded | `launchctl print gui/$(id -u)/com.dolr-ai.yral-rishi-agent.pull-sentry-baseline` to verify state; if missing, re-run installer |
| stdout log says `OK …` but CSV has no new rows | macOS file path issue (rare) | Inspect the launchd `WorkingDirectory` and the script's `LATENCY_BASELINE_FOLDER` constant |

## Stop / uninstall

Per CONSTRAINTS A1 (no-delete) the plist file itself is preserved when
the job is unloaded — only the schedule is stopped:

```bash
launchctl bootout gui/$(id -u)/com.dolr-ai.yral-rishi-agent.pull-sentry-baseline
```

To rotate the Keychain token without touching launchd:

```bash
security add-generic-password -U -a dolr-ai -s SENTRY_AUTH_TOKEN \
    -w '<new-token>' -T /usr/bin/security
```

The next 9 a.m. run picks up the new token automatically.

## Why this exists (one paragraph)

CONSTRAINTS row **E1** upgraded latency from "never regress" to "≥50%
faster than Python yral-chat-ai" on **2026-04-24**. To prove that, we
need a daily snapshot of the live service's actual production latency.
Sentry already records every request's duration; this script just asks
Sentry's API for the aggregate every morning, stores the answer, and
keeps a running history that the future v2 CI latency gate (per E1) and
hourly production comparison (per A6 cutover safety) both read from.

## Related

- CONSTRAINTS rows: A7 (Sentry host), D1 (secrets in Keychain not env),
  D7+D8 (declarative secrets manifest), E1 (50%-faster), I7 (Sentry API
  pre-authorized).
- Parent folder README: `../README.md` — overall purpose of the
  latency-baseline-capture folder, plus the eventual schema of
  `latency-baselines.md` once the data has stabilised.
- Session-1 agent definition:
  `~/Claude Projects/yral-rishi-agent/.claude/agents/session-1-infra-cluster.md`
- Memory: `~/.claude/projects/-Users-rishichadha/memory/project_v2_first_build_task_sentry_baseline_pull.md`
