#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  pull-sentry-baseline.py                                                  ║
# ║                                                                            ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                              ║
# ║  Pull p50/p95/p99 latency + error counts for the live yral-chat-ai        ║
# ║  service from sentry.rishi.yral.com once a day, append to a CSV, and      ║
# ║  rewrite a one-page markdown summary that says "the numbers v2 must beat".║
# ║                                                                            ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                         ║
# ║  CONSTRAINTS row E1 says v2 must run at least 50% faster than the live    ║
# ║  Python yral-chat-ai. To prove that we beat it, we first need to know     ║
# ║  what "it" is — the actual production latency users see today. Sentry     ║
# ║  already records every request's duration, so this script asks Sentry's   ║
# ║  API every morning at 9 a.m. IST: "for each thing yral-chat-ai does,      ║
# ║  what's the typical (p50), the slow (p95), and the worst (p99) over the   ║
# ║  last 24 hours?" Then it writes the answer to a CSV that grows over time, ║
# ║  plus a markdown file with just the most-recent snapshot for at-a-glance  ║
# ║  reading. Run by macOS launchd; uses only Python's standard library so    ║
# ║  there is nothing to install beyond Python 3.10+.                         ║
# ║                                                                            ║
# ║  🔗 HOW IT FITS WITH OTHER FILES                                           ║
# ║  - Output 1: `../daily-baseline.csv` — append-only history, one row per   ║
# ║    (date, Sentry transaction) pair. Read by future v2 CI latency gate     ║
# ║    (per E1) to compute the 0.5× target each PR must beat.                 ║
# ║  - Output 2: `../latest-baseline.md` — fresh markdown summary, overwritten║
# ║    every run. The file Rishi reads in MASTER-STATUS or by hand to see     ║
# ║    "where do we stand vs the live service today?"                         ║
# ║  - Schedule: `com.dolr-ai.yral-rishi-agent.pull-sentry-baseline.plist`    ║
# ║    in this same folder runs this script under launchd at 9 a.m. local.   ║
# ║  - Secret: macOS Keychain entry (account `dolr-ai`, service               ║
# ║    `SENTRY_AUTH_TOKEN`) — fetched at runtime via `security`. Declared in  ║
# ║    `secrets.yaml` next to this file (per CONSTRAINTS D7+D8).              ║
# ║  - CONSTRAINTS A7 + I7: Sentry host is always `sentry.rishi.yral.com`    ║
# ║    (NOT `apm.yral.com`); aggregated reads are pre-authorized so this     ║
# ║    script does not need a per-run Rishi YES.                              ║
# ║                                                                            ║
# ║  📥 INPUTS                                                                 ║
# ║  - macOS Keychain entry holding the Sentry auth token (read-only scope)  ║
# ║  - Optional environment variables for org/project slug overrides          ║
# ║                                                                            ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                 ║
# ║  - Appends rows to `../daily-baseline.csv` (creates the file with header  ║
# ║    on first run)                                                          ║
# ║  - Overwrites `../latest-baseline.md` with the freshest snapshot          ║
# ║  - Writes a one-line success log to stdout                                ║
# ║  - Exit code 0 on success, 1 on any failure (so launchd flags it)        ║
# ║                                                                            ║
# ║  ⭐ START HERE                                                             ║
# ║  Read `main()` first; every other function in this file is called by    ║
# ║  main(). Functions appear in the order main() calls them — top of file  ║
# ║  = first thing that runs, bottom of file = last.                         ║
# ║                                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Standard-library imports only — no third-party dependencies, so the launchd
# job has nothing to install and no virtualenv to activate.
import csv  # writes the append-only daily-baseline rows in CSV format
import json  # parses Sentry's HTTP response body
import os  # reads optional environment-variable overrides for org/project slug
import pathlib  # resolves paths relative to this file in a cross-shell-safe way
import subprocess  # invokes the macOS `security` command-line tool to read Keychain
import sys  # writes errors to stderr and sets the process exit code
import urllib.error  # captures HTTP errors raised by urllib.request.urlopen
import urllib.parse  # builds the Sentry query string with proper encoding
import urllib.request  # performs the HTTPS GET against the Sentry Discover API
from datetime import datetime, timezone  # stamps each row with an absolute UTC date


# ───────────────── Constants — anchored to CONSTRAINTS rows ──────────────────

# Per CONSTRAINTS A7: Sentry for ALL v2 work is `sentry.rishi.yral.com`,
# never `apm.yral.com`. Hardcoded here on purpose so a typo or an env override
# cannot accidentally route us to the team-shared Sentry.
SENTRY_HOST = "sentry.rishi.yral.com"

# Default organization slug on sentry.rishi.yral.com. If the slug is something
# else, set the SENTRY_ORGANIZATION_SLUG environment variable in the launchd
# plist (it overrides this default at runtime).
DEFAULT_SENTRY_ORGANIZATION_SLUG = "dolr-ai"

# Sentry project slug for the live Python yral-chat-ai service whose latency
# v2 must beat per CONSTRAINTS row E1.
DEFAULT_SENTRY_PROJECT_SLUG = "yral-chat-ai"

# macOS Keychain coordinates used by `security find-generic-password`.
# `-a dolr-ai -s SENTRY_AUTH_TOKEN -w` reads only the password (no metadata).
KEYCHAIN_ACCOUNT_NAME = "dolr-ai"
KEYCHAIN_SERVICE_NAME = "SENTRY_AUTH_TOKEN"

# How far back each daily pull looks. 24h matches the cron cadence: every
# morning we summarise the previous day so the CSV row covers a full window.
SENTRY_STATS_LOOKBACK_PERIOD = "24h"

# Cap the number of transactions returned per call. 30 covers the user-
# interactive endpoints (chat, conversations, influencers) and any noisy
# background routes; if the live service ever has more, raise this number.
TOP_TRANSACTIONS_PER_REQUEST = 30

# Network timeout — Sentry usually answers in <2s; 30s is generous for a
# slow morning while still bounding the launchd job runtime.
HTTP_REQUEST_TIMEOUT_SECONDS = 30

# Output file paths. Resolved relative to this script so it works whether
# launchd runs it via absolute path or whether a developer runs it manually.
THIS_FILE_DIRECTORY = pathlib.Path(__file__).resolve().parent
LATENCY_BASELINE_FOLDER = THIS_FILE_DIRECTORY.parent
DAILY_BASELINE_CSV_PATH = LATENCY_BASELINE_FOLDER / "daily-baseline.csv"
LATEST_BASELINE_MARKDOWN_PATH = LATENCY_BASELINE_FOLDER / "latest-baseline.md"

# CSV column order. Anchoring this in code (not in the file's header row)
# means future readers see the source of truth here, and the script does
# not have to reconcile with whatever the file already had.
DAILY_BASELINE_CSV_HEADER = [
    "pull_date_utc",
    "sentry_organization_slug",
    "sentry_project_slug",
    "transaction_name",
    "request_count",
    "p50_milliseconds",
    "p95_milliseconds",
    "p99_milliseconds",
    "failure_rate",
    "lookback_period",
]


# ─────────────────────────────── Entry point ─────────────────────────────────


def main() -> int:
    """⭐ Entry point — orchestrates the whole pull.

    WHAT:  fetch the auth token from Keychain, ask Sentry for top
           transactions over the last 24 hours, append every transaction
           as a CSV row, then overwrite the markdown summary.
    WHEN:  launchd fires this every morning at 9 a.m. local time; a
           developer can also run it by hand to backfill or to debug.
    WHY:   E1 (50%-faster HARD constraint) needs a moving target; this
           is the script that produces that target every single day.
    """
    # Step 1 — resolve config, allowing env overrides without code changes.
    sentry_organization_slug = os.environ.get(
        "SENTRY_ORGANIZATION_SLUG", DEFAULT_SENTRY_ORGANIZATION_SLUG
    )
    sentry_project_slug = os.environ.get(
        "SENTRY_PROJECT_SLUG", DEFAULT_SENTRY_PROJECT_SLUG
    )

    # Step 2 — read the Sentry auth token from macOS Keychain. Failing here
    # means the Keychain entry is missing; the function raises a clear error.
    try:
        sentry_authentication_token = fetch_sentry_authentication_token_from_keychain()
    except RuntimeError as keychain_error:
        write_error_line(f"Could not read Sentry token from Keychain: {keychain_error}")
        return 1

    # Step 3 — call the Sentry Discover API for the top transactions.
    try:
        latency_metrics_rows = fetch_latency_metrics_from_sentry(
            sentry_authentication_token=sentry_authentication_token,
            sentry_organization_slug=sentry_organization_slug,
            sentry_project_slug=sentry_project_slug,
        )
    except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as sentry_error:
        write_error_line(f"Sentry API call failed: {sentry_error}")
        return 1

    # If Sentry returned an empty list the project may be quiet for 24 hours
    # or the slug is wrong. Surface this loudly instead of silently writing
    # nothing — silent zero-row days would mask a real outage.
    if not latency_metrics_rows:
        write_error_line(
            "Sentry returned 0 transactions for "
            f"organization={sentry_organization_slug} "
            f"project={sentry_project_slug}. "
            "Verify the project slug and that traffic flowed in the last 24h."
        )
        return 1

    # Step 4 — record an absolute UTC date so rows are still meaningful when
    # read months later regardless of the laptop's timezone.
    pull_date_utc = datetime.now(tz=timezone.utc).date().isoformat()

    # Step 5 — append rows to the CSV (create the file with header on first run).
    append_metrics_to_daily_baseline_csv(
        latency_metrics_rows=latency_metrics_rows,
        pull_date_utc=pull_date_utc,
        sentry_organization_slug=sentry_organization_slug,
        sentry_project_slug=sentry_project_slug,
        csv_path=DAILY_BASELINE_CSV_PATH,
    )

    # Step 6 — overwrite the markdown snapshot so anyone reading it sees the
    # freshest numbers without scrolling through the CSV.
    write_latest_baseline_markdown_summary(
        latency_metrics_rows=latency_metrics_rows,
        pull_date_utc=pull_date_utc,
        sentry_organization_slug=sentry_organization_slug,
        sentry_project_slug=sentry_project_slug,
        markdown_path=LATEST_BASELINE_MARKDOWN_PATH,
    )

    # Step 7 — log success to stdout. launchd captures this in its log file
    # so a quick `tail` shows whether the morning pull worked.
    print(
        f"OK pull-sentry-baseline {pull_date_utc} "
        f"organization={sentry_organization_slug} "
        f"project={sentry_project_slug} "
        f"rows={len(latency_metrics_rows)}"
    )
    return 0


# ────────────────────── Helpers, in main()-call order ────────────────────────


def fetch_sentry_authentication_token_from_keychain() -> str:
    """Read the Sentry auth token out of macOS Keychain.

    WHAT:  shell out to `security find-generic-password -a <account>
           -s <service> -w`, which prints just the password to stdout.
    WHEN:  called once per run, before any Sentry HTTP call.
    WHY:   per CONSTRAINTS D1, secrets must never live in code, env files
           checked into git, or process argument lists. Keychain on macOS
           is the same trust store Rishi uses elsewhere on his laptop;
           rotating the token = `security add-generic-password -U ...` once.
    """
    # `security` is part of macOS — no install step required.
    keychain_command = [
        "security",
        "find-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT_NAME,
        "-s",
        KEYCHAIN_SERVICE_NAME,
        "-w",
    ]

    try:
        # capture_output=True keeps the secret out of the parent's stdout.
        # check=True turns a non-zero exit (no entry, locked Keychain) into a
        # CalledProcessError that we translate into a friendlier RuntimeError.
        completed_process = subprocess.run(
            keychain_command,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except FileNotFoundError as security_missing:
        # macOS-only tool. Running this script on Linux is a setup mistake.
        raise RuntimeError(
            "`security` command not found — this script requires macOS."
        ) from security_missing
    except subprocess.CalledProcessError as keychain_lookup_failed:
        raise RuntimeError(
            "Keychain entry missing. Add it once with: "
            "security add-generic-password -U "
            f"-a {KEYCHAIN_ACCOUNT_NAME} -s {KEYCHAIN_SERVICE_NAME} "
            "-w '<paste-your-token>' -T /usr/bin/security"
        ) from keychain_lookup_failed
    except subprocess.TimeoutExpired as keychain_timeout:
        # Locked Keychain prompts for a password and blocks. Timeout makes
        # that obvious instead of letting launchd hang for 10 minutes.
        raise RuntimeError(
            "Reading Keychain timed out — is the login keychain locked?"
        ) from keychain_timeout

    # Strip the trailing newline `security` adds. Empty after strip = bad
    # entry (entry exists but has no password set). Treat that as missing.
    sentry_authentication_token = completed_process.stdout.strip()
    if not sentry_authentication_token:
        raise RuntimeError(
            f"Keychain entry {KEYCHAIN_SERVICE_NAME} exists but is empty."
        )
    return sentry_authentication_token


def fetch_latency_metrics_from_sentry(
    sentry_authentication_token: str,
    sentry_organization_slug: str,
    sentry_project_slug: str,
) -> list[dict]:
    """Call the Sentry Discover API for top transactions with p50/p95/p99.

    WHAT:  HTTPS GET to the Sentry Discover endpoint, asking for the top N
           transactions (sorted by request count) over the last 24h together
           with p50/p95/p99 of transaction.duration and the failure rate.
    WHEN:  called once per run, immediately after the Keychain lookup.
    WHY:   Discover is the only Sentry API surface that returns aggregated
           percentiles per transaction in one call. Per CONSTRAINTS A14
           we deliberately avoid pulling raw event bodies — aggregates only.
    """
    # Build the query string. Sentry treats every `field=...` as one column;
    # repeating field= adds another column to the response.
    sentry_discover_query_parameters = [
        ("field", "transaction"),
        ("field", "count()"),
        ("field", "p50(transaction.duration)"),
        ("field", "p95(transaction.duration)"),
        ("field", "p99(transaction.duration)"),
        ("field", "failure_rate()"),
        ("query", f"event.type:transaction project:{sentry_project_slug}"),
        ("statsPeriod", SENTRY_STATS_LOOKBACK_PERIOD),
        ("sort", "-count"),
        ("per_page", str(TOP_TRANSACTIONS_PER_REQUEST)),
        # `referrer` lets the Sentry side classify our traffic in their own
        # logs — a courtesy to the self-hosted Sentry admin (also Rishi).
        ("referrer", "yral-rishi-agent-baseline-pull"),
    ]
    sentry_discover_url = (
        f"https://{SENTRY_HOST}/api/0/organizations/"
        f"{sentry_organization_slug}/events/?"
        + urllib.parse.urlencode(sentry_discover_query_parameters)
    )

    # Bearer-token auth per Sentry's documented scheme.
    sentry_discover_request = urllib.request.Request(
        sentry_discover_url,
        headers={
            "Authorization": f"Bearer {sentry_authentication_token}",
            "Accept": "application/json",
            "User-Agent": "yral-rishi-agent-baseline-pull/1.0",
        },
    )

    # `with` closes the underlying socket promptly. read() returns bytes.
    with urllib.request.urlopen(
        sentry_discover_request, timeout=HTTP_REQUEST_TIMEOUT_SECONDS
    ) as sentry_response:
        if sentry_response.status != 200:
            raise RuntimeError(
                f"Sentry returned HTTP {sentry_response.status}"
            )
        sentry_response_body = sentry_response.read().decode("utf-8")

    # Parse JSON. A malformed body throws JSONDecodeError; main() catches it.
    sentry_response_payload = json.loads(sentry_response_body)

    # Sentry wraps the rows under "data" — guard against unexpected shapes.
    if "data" not in sentry_response_payload:
        raise RuntimeError(
            f"Unexpected Sentry response shape: keys={list(sentry_response_payload)}"
        )

    return sentry_response_payload["data"]


def append_metrics_to_daily_baseline_csv(
    latency_metrics_rows: list[dict],
    pull_date_utc: str,
    sentry_organization_slug: str,
    sentry_project_slug: str,
    csv_path: pathlib.Path,
) -> None:
    """Append one row per transaction to the daily-baseline CSV.

    WHAT:  open the CSV in append mode, write the header on first run,
           then write one row for each transaction Sentry returned.
    WHEN:  after the Sentry call succeeds; before the markdown rewrite.
    WHY:   the CSV is the historical record that lets the future v2 CI
           latency gate compute the 0.5× target — it must keep growing,
           never get truncated, and always carry full row context (date +
           org + project) so a future query is unambiguous.
    """
    # Ensure the parent directory exists. parents=True is a no-op if it
    # already does; exist_ok=True keeps re-runs idempotent.
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_file_already_exists = csv_path.exists()

    # `newline=""` lets the csv module own line termination — required on
    # macOS so rows do not pick up a stray \r.
    with csv_path.open(mode="a", encoding="utf-8", newline="") as csv_file_handle:
        csv_writer = csv.writer(csv_file_handle)
        if not csv_file_already_exists:
            csv_writer.writerow(DAILY_BASELINE_CSV_HEADER)

        for sentry_transaction_row in latency_metrics_rows:
            csv_writer.writerow(
                [
                    pull_date_utc,
                    sentry_organization_slug,
                    sentry_project_slug,
                    sentry_transaction_row.get("transaction", ""),
                    int(sentry_transaction_row.get("count()", 0) or 0),
                    format_milliseconds(
                        sentry_transaction_row.get("p50(transaction.duration)")
                    ),
                    format_milliseconds(
                        sentry_transaction_row.get("p95(transaction.duration)")
                    ),
                    format_milliseconds(
                        sentry_transaction_row.get("p99(transaction.duration)")
                    ),
                    format_failure_rate(
                        sentry_transaction_row.get("failure_rate()")
                    ),
                    SENTRY_STATS_LOOKBACK_PERIOD,
                ]
            )


def write_latest_baseline_markdown_summary(
    latency_metrics_rows: list[dict],
    pull_date_utc: str,
    sentry_organization_slug: str,
    sentry_project_slug: str,
    markdown_path: pathlib.Path,
) -> None:
    """Overwrite the markdown summary with the freshest snapshot.

    WHAT:  emit a single markdown file with one table of the day's top
           transactions sorted by request count, plus a header that says
           when the pull happened and what v2 must beat.
    WHEN:  last step of every run, so the file always reflects the most
           recent pull and never gets stale relative to the CSV.
    WHY:   the CSV is for machines (CI gate, future analytics); this file
           is for Rishi to read in 10 seconds and see today's numbers.
    """
    # Build the markdown line by line. Using a list + "\n".join is faster
    # and easier to reason about than repeated string concatenation.
    markdown_lines: list[str] = []
    markdown_lines.append("# Latest Sentry Baseline — yral-chat-ai")
    markdown_lines.append("")
    markdown_lines.append(
        f"_Pulled: {pull_date_utc} (UTC). Lookback: {SENTRY_STATS_LOOKBACK_PERIOD}._"
    )
    markdown_lines.append(
        f"_Source: `https://{SENTRY_HOST}` "
        f"organization=`{sentry_organization_slug}` "
        f"project=`{sentry_project_slug}` "
        f"(per CONSTRAINTS A7 + I7)._"
    )
    markdown_lines.append("")
    markdown_lines.append(
        "Per CONSTRAINTS row E1, v2 must run at least **50% faster** than the "
        "numbers below on every user-interactive endpoint. The figures below "
        "are what each PR's latency gate compares against."
    )
    markdown_lines.append("")
    markdown_lines.append(
        "| Transaction | Requests | p50 (ms) | p95 (ms) | p99 (ms) | Failure rate |"
    )
    markdown_lines.append(
        "|---|---:|---:|---:|---:|---:|"
    )

    for sentry_transaction_row in latency_metrics_rows:
        markdown_lines.append(
            "| "
            f"`{sentry_transaction_row.get('transaction', '')}` | "
            f"{int(sentry_transaction_row.get('count()', 0) or 0):,} | "
            f"{format_milliseconds(sentry_transaction_row.get('p50(transaction.duration)'))} | "
            f"{format_milliseconds(sentry_transaction_row.get('p95(transaction.duration)'))} | "
            f"{format_milliseconds(sentry_transaction_row.get('p99(transaction.duration)'))} | "
            f"{format_failure_rate(sentry_transaction_row.get('failure_rate()'))} |"
        )

    markdown_lines.append("")
    markdown_lines.append(
        "_Generated by `scripts/pull-sentry-baseline.py`. The full append-only "
        "history lives in `daily-baseline.csv` next to this file._"
    )
    markdown_lines.append("")

    # Write atomically: write to a sibling temp file, then rename. A crash
    # mid-write therefore can never leave a half-written markdown summary.
    markdown_temporary_path = markdown_path.with_suffix(".md.partial")
    markdown_temporary_path.write_text(
        "\n".join(markdown_lines), encoding="utf-8"
    )
    markdown_temporary_path.replace(markdown_path)


def format_milliseconds(raw_milliseconds_value) -> str:
    """Render Sentry's float milliseconds as a clean integer string.

    WHAT:  coerce None / float / int to a "1234" style string, or "n/a".
    WHEN:  called from the CSV writer and the markdown writer for every cell.
    WHY:   Sentry returns floats with many decimals; the CSV/markdown reader
           cares about whole milliseconds, and a missing value should look
           obvious instead of silently becoming "0".
    """
    if raw_milliseconds_value is None:
        return "n/a"
    return str(int(round(float(raw_milliseconds_value))))


def format_failure_rate(raw_failure_rate_value) -> str:
    """Render Sentry's 0..1 failure-rate float as a percentage.

    WHAT:  coerce None / float to a "0.42%" style string, or "n/a".
    WHEN:  called from the CSV writer and the markdown writer.
    WHY:   percentages are how humans read failure rates; aligns with how
           the rest of the latency-baseline doc presents error rates.
    """
    if raw_failure_rate_value is None:
        return "n/a"
    return f"{float(raw_failure_rate_value) * 100:.2f}%"


def write_error_line(error_message: str) -> None:
    """Print a one-line error to stderr.

    WHAT:  send `error_message` to sys.stderr with a newline.
    WHEN:  whenever main() catches a recoverable failure before exiting 1.
    WHY:   launchd routes stderr to its log file (StandardErrorPath); a
           single function keeps the format consistent so a future grep
           for failures finds them all.
    """
    print(f"ERROR pull-sentry-baseline: {error_message}", file=sys.stderr)


# ───────────────────────── Entry-point dispatch ─────────────────────────────


# When the file is run directly (launchd, `python3 pull-sentry-baseline.py`,
# or `./pull-sentry-baseline.py`) main()'s return code becomes the exit code.
# When the file is imported (e.g. for testing) nothing runs automatically.
if __name__ == "__main__":
    sys.exit(main())


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - com.dolr-ai.yral-rishi-agent.pull-sentry-baseline.plist
#       launchd schedule that runs this script every morning at 9 a.m. local.
# - install-launchd-job.sh
#       Helper that loads the plist into launchd (`launchctl bootstrap`).
# - secrets.yaml
#       Per-folder secrets manifest declaring SENTRY_AUTH_TOKEN (D7+D8).
# - README.md
#       Install instructions, troubleshooting, expected output.
# - ../README.md
#       The latency-baseline-capture folder readme (overall purpose, E1 link).
# - ../../CONSTRAINTS.md
#       Rows A7, D1, D7, D8, E1, I7 are the ones this script directly serves.
# ══════════════════════════════════════════════════════════════════════════
