"""On-demand ETL drain + reconciliation report.

Pieces B + C of the on-demand drain system (2026-06-11 plan). Sits on
top of the existing 3-piece ETL (exporter on rishi-1, importer + integrity
verifier in V2) without modifying them — it just calls their `run_once`
helpers in a tight loop and reads the persisted results.

Two public entry points consumed by routes/health.py:

  drain(pool, deadline_sec) ─ POST /admin/etl/drain
    Tight loop: run_once(importer) until 2 consecutive empties or
    deadline; then run_once(integrity) until layer-coverage check or
    deadline. Returns the reconciliation report inline so the workflow
    has one round-trip.

  reconciliation(pool) ─ GET /admin/etl/reconciliation
    Pure read; no side effects. Combines:
      - V2 row counts (live COUNT(*))
      - chat-ai row counts (from the latest hourly integrity payload)
      - Deliberate-skip totals (etl_skipped_rows, by reason)
      - Integrity layer counts in the last 24h
      - Verdict: GREEN / DRAIN_AGAIN / INVESTIGATE (per the plan §3)

Verdict rules (verbatim from plan §3):
  GREEN          all deltas explained by deliberate skips
                 AND all 3 integrity layers have ≥1 pass in last 24h
                 AND chat-ai exporter heartbeat fresh (< 30 min old)
  DRAIN_AGAIN    a row count delta is NOT covered by skips — likely
                 in-flight, re-trigger drain to catch up
  INVESTIGATE    integrity layer FAILED in last 24h
                 OR exporter heartbeat stale > 30 min
                 OR drain hit its deadline without reaching steady-state
                 OR a brand-new skip class appeared

Tables in scope: the three rows the ETL covers today
(ai_influencers / conversations / messages). system_instructions_history
is V2-native (Coach edits live there; chat-ai never had the table) — it
shows up in the report as `kind: "v2_native"` with chat_ai_count: null
and does NOT factor into the verdict. Per the plan §9 + Rishi's pre-
decision: this is the simpler path; extending the exporter to cover a
V2-only table is a future change.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Drain budget — outside the drain workflow, hard timeout is the
# workflow's `timeout-minutes`. Inside Python we set our own budget so
# a stuck S3 doesn't pin the request indefinitely (FastAPI cancels at
# the H2 socket level eventually, but we want a structured timeout).
DEFAULT_DRAIN_DEADLINE_SEC = 180.0

# Wait between consecutive run_once calls during a drain. Importer
# spec calls for "5 sec instead of 5 min until no new files for 30 sec";
# we use 5s ticks and consider 2 consecutive empty runs (=10s of no
# new files) as steady-state, which is more responsive than 30s.
DRAIN_TICK_SEC = 5.0
STEADY_STATE_TICKS = 2

# Stale heartbeat threshold for the verdict. Matches the plan's "30 min"
# cutoff above which the exporter is considered possibly stuck.
HEARTBEAT_STALE_SEC = 30 * 60

# Layers required to have ≥1 PASS in the last 24h for GREEN.
REQUIRED_LAYERS = ("hourly", "sample", "sentinel")

# Skip reasons we consider DELIBERATE (these explain row-count deltas
# without triggering DRAIN_AGAIN). Any reason not in this set surfaces
# as INVESTIGATE.
DELIBERATE_SKIP_REASONS = frozenset({"conflict", "orphan"})

# V2-native tables — these appear in the report for completeness but
# don't factor into delta-explanation. chat-ai never had them.
V2_NATIVE_TABLES = ("system_instructions_history",)

# Tables the ETL covers — kept in sync with etl_chat_ai.SYNCED_TABLES.
ETL_COVERED_TABLES = ("ai_influencers", "conversations", "messages")


# ─── drain ────────────────────────────────────────────────────────────────


async def drain(pool, deadline_sec: float = DEFAULT_DRAIN_DEADLINE_SEC) -> dict:
    """Force importer + integrity ticks until steady state.

    Returns a dict carrying the reconciliation report plus drain
    telemetry (iteration counts, deadline-hit flag, elapsed seconds).
    Non-side-effecting on chat-ai (it's read-only by IAM); side effects
    on V2 are exactly what the regular 5-min loop would do, just sooner.

    The drain is idempotent: importer dedupes via etl_processed_files
    PK; integrity verifier dedupes via the same etl_integrity_results
    file-uniqueness it uses normally. Re-trigger is safe."""
    from services import etl_chat_ai, etl_integrity

    started_at = time.monotonic()
    deadline = started_at + deadline_sec
    importer_ticks = 0
    integrity_ticks = 0
    consecutive_empty = 0
    importer_hit_deadline = False
    integrity_hit_deadline = False
    importer_total_files = 0

    # ─── Phase 1: drain the importer queue ─────────────────────────
    while True:
        if time.monotonic() >= deadline:
            importer_hit_deadline = True
            break
        try:
            result = await etl_chat_ai.run_once(pool)
        except Exception as e:
            logger.warning("drain: importer run_once raised %s", e)
            result = {"status": "error", "files_processed": 0}
        importer_ticks += 1
        files = int(result.get("files_processed") or 0)
        importer_total_files += files
        logger.info(
            "drain importer tick=%d files=%d consecutive_empty=%d",
            importer_ticks,
            files,
            consecutive_empty,
        )
        if files == 0:
            consecutive_empty += 1
            if consecutive_empty >= STEADY_STATE_TICKS:
                break
        else:
            consecutive_empty = 0
        # Sleep before next tick, capping at remaining deadline.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            importer_hit_deadline = True
            break
        await asyncio.sleep(min(DRAIN_TICK_SEC, remaining))

    # ─── Phase 2: drive integrity verifier until layers fresh ──────
    # We want the latest hourly + sample + sentinel results in
    # etl_integrity_results to be newer than the drain-start wall
    # clock — that's what gives the reconciliation report 24h-fresh
    # evidence.
    #
    # Two prior bugs lived here (caught in prod 2026-06-12 when the
    # drain endpoint started 500'ing under real Postgres):
    #   1. The watermark was built as
    #        datetime.fromtimestamp(started_at, tz=timezone.utc)
    #      where `started_at = time.monotonic()`. monotonic() is NOT
    #      epoch-relative — it's seconds since an arbitrary boot-time
    #      reference. Feeding it through fromtimestamp() produces
    #      a 1970-relative timestamp ("1970-01-05T07:07:56Z" in prod),
    #      which then makes the freshness comparison match every row.
    #   2. The watermark was passed to fetchrow() as an `.isoformat()`
    #      STRING with `WHERE verified_at > $1::timestamptz`. asyncpg
    #      enforces parameter type at protocol-level — `::timestamptz`
    #      casting inside the SQL doesn't change that. asyncpg rejects
    #      strings on timestamptz parameters with `DataError: expected
    #      a datetime.date or datetime.datetime instance, got 'str'`.
    #
    # Fix: capture a real `datetime.now(tz=UTC)` once at drain-start
    # (wall-clock; the right reference for "newer than" comparisons
    # against `verified_at`) and pass it directly to asyncpg.
    started_dt = datetime.now(timezone.utc)
    while True:
        if time.monotonic() >= deadline:
            integrity_hit_deadline = True
            break
        try:
            await etl_integrity.run_once(pool)
        except Exception as e:
            logger.warning("drain: integrity run_once raised %s", e)
        integrity_ticks += 1
        # Check if each required layer has a row newer than drain-start.
        # `etl_integrity_results.verified_at` is `TIMESTAMP` (no tz —
        # migration 020). asyncpg refuses to compare a tz-aware datetime
        # against a naive column with `DataError: can't subtract
        # offset-naive and offset-aware datetimes` — strip the UTC
        # tzinfo at the call site. The watermark itself is conceptually
        # UTC (we built it via datetime.now(timezone.utc)); we just hand
        # asyncpg the naive form Postgres expects. Schema fix
        # (TIMESTAMPTZ on verified_at) is a separate post-cutover
        # hygiene item — touching it requires re-running the integrity
        # tests against the new column type.
        fresh = await pool.fetchrow(
            """
            SELECT
                MAX(CASE WHEN layer='hourly'   THEN verified_at END) AS hourly_at,
                MAX(CASE WHEN layer='sample'   THEN verified_at END) AS sample_at,
                MAX(CASE WHEN layer='sentinel' THEN verified_at END) AS sentinel_at
            FROM etl_integrity_results
            WHERE verified_at > $1
            """,
            started_dt.replace(tzinfo=None),
        )
        if fresh and all(
            fresh[c] is not None for c in ("hourly_at", "sample_at", "sentinel_at")
        ):
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            integrity_hit_deadline = True
            break
        await asyncio.sleep(min(DRAIN_TICK_SEC, remaining))

    elapsed = round(time.monotonic() - started_at, 2)
    report = await reconciliation(pool)
    report["drain"] = {
        "started_at": started_dt.isoformat(),
        "elapsed_sec": elapsed,
        "importer_ticks": importer_ticks,
        "importer_total_files_processed": importer_total_files,
        "integrity_ticks": integrity_ticks,
        "importer_hit_deadline": importer_hit_deadline,
        "integrity_hit_deadline": integrity_hit_deadline,
        "deadline_sec": deadline_sec,
    }
    # If we ran out of time before the importer reached steady-state OR
    # before all integrity layers landed, the verdict can't be GREEN even
    # if the row-count math happens to balance — surface the gap.
    if importer_hit_deadline or integrity_hit_deadline:
        if report["verdict"] == "GREEN":
            report["verdict"] = "INVESTIGATE"
            report["verdict_explanation"] = (
                "Drain hit its deadline before reaching steady state; "
                "row-count math passed but evidence is incomplete. "
                "Re-run the drain."
            )
            report.setdefault("warnings", []).append("drain_deadline_hit")
    return report


# ─── reconciliation report ────────────────────────────────────────────────


async def reconciliation(pool) -> dict:
    """Pure read. Composes the report described in plan §3.

    Computed on-demand; no caching. Roughly 4 SQL roundtrips + 3
    COUNT(*) per ETL table — sub-second on a healthy DB."""
    now = datetime.now(timezone.utc)

    chat_ai_counts, chat_ai_export_ts = await _chat_ai_counts_from_hourly_payload(pool)
    v2_counts = await _v2_row_counts(pool)
    skip_breakdown = await _skip_breakdown_by_table(pool)
    integrity_summary = await _integrity_summary_24h(pool)
    v2_latest_import = await _v2_latest_import_ts(pool)
    new_skip_reasons = await _new_skip_reasons(pool)

    tables: list[dict] = []
    warnings: list[str] = []
    blocking_issues: list[str] = []

    # Tables the ETL covers — these are the verdict-bearing rows.
    for tbl in ETL_COVERED_TABLES:
        ca = chat_ai_counts.get(tbl)
        v2 = v2_counts.get(tbl, 0)
        skips = skip_breakdown.get(tbl, {})
        if ca is None:
            # We don't have a fresh hourly payload — surface the gap.
            tables.append(
                {
                    "name": tbl,
                    "kind": "etl_covered",
                    "chat_ai_count": None,
                    "v2_count": v2,
                    "delta": None,
                    "skipped_breakdown": skips,
                    "skips_explain_delta": False,
                    "note": "no hourly integrity payload available — chat-ai count unknown",
                }
            )
            continue
        delta = ca - v2
        skip_total = sum(skips.values())
        explained = delta == skip_total
        tables.append(
            {
                "name": tbl,
                "kind": "etl_covered",
                "chat_ai_count": ca,
                "v2_count": v2,
                "delta": delta,
                "skipped_breakdown": skips,
                "skips_explain_delta": explained,
            }
        )

    # V2-native tables — informational only, never block the verdict.
    for tbl in V2_NATIVE_TABLES:
        v2 = await _safe_table_count(pool, tbl)
        tables.append(
            {
                "name": tbl,
                "kind": "v2_native",
                "chat_ai_count": None,
                "v2_count": v2,
                "delta": None,
                "skipped_breakdown": {},
                "note": "V2-native table (Coach edits); chat-ai never had it",
            }
        )

    verdict, verdict_explanation = _compute_verdict(
        tables=tables,
        integrity=integrity_summary,
        chat_ai_export_ts=chat_ai_export_ts,
        new_skip_reasons=new_skip_reasons,
        now=now,
        warnings=warnings,
        blocking_issues=blocking_issues,
    )

    lag_seconds: int | None = None
    if chat_ai_export_ts is not None and v2_latest_import is not None:
        lag_seconds = max(
            0, int((v2_latest_import - chat_ai_export_ts).total_seconds())
        )

    return {
        "as_of": now.isoformat(),
        "chat_ai_latest_export_ts": chat_ai_export_ts.isoformat()
        if chat_ai_export_ts
        else None,
        "v2_latest_import_ts": v2_latest_import.isoformat()
        if v2_latest_import
        else None,
        "lag_seconds": lag_seconds,
        "verdict": verdict,
        "verdict_explanation": verdict_explanation,
        "tables": tables,
        "integrity_last_24h": integrity_summary,
        "warnings": warnings,
        "blocking_issues": blocking_issues,
    }


# ─── helpers (private) ────────────────────────────────────────────────────


async def _chat_ai_counts_from_hourly_payload(
    pool,
) -> tuple[dict[str, int], datetime | None]:
    """The exporter's hourly integrity layer emits a JSON payload that
    includes chat-ai's per-table row counts. We read the latest one
    from etl_integrity_results.details. If absent, return ({}, None)."""
    row = await pool.fetchrow(
        """
        SELECT details, snapshot_iso, verified_at
        FROM etl_integrity_results
        WHERE layer = 'hourly'
        ORDER BY verified_at DESC LIMIT 1
        """
    )
    if not row:
        return {}, None
    details = row["details"]
    if isinstance(details, str):
        import json as _json

        try:
            details = _json.loads(details)
        except (_json.JSONDecodeError, TypeError):
            return {}, None
    if not isinstance(details, dict):
        return {}, None
    # Prefer snapshot_iso (chat-ai-side wall clock) over verified_at
    # (V2-side processing time) for the "latest export" timestamp.
    # Computed BEFORE the per_table early-return so both exit paths
    # return a datetime, not a raw string. (asyncpg returns ISO-string
    # timestamps from the JSONB payload; the lag-seconds arithmetic
    # downstream needs a datetime.)
    #
    # `verified_at` is `TIMESTAMP` (no tz, migration 020) — asyncpg
    # returns tz-naive. The downstream lag-seconds subtraction does
    # `v2_latest_import - chat_ai_export_ts` and `now - chat_ai_export_ts`
    # where the other operands are tz-aware UTC. Mixing raises
    # `TypeError: can't subtract offset-naive and offset-aware datetimes`
    # (caught in prod 2026-06-12 on /admin/etl/reconciliation).
    # Tag tzinfo=UTC on the fallback path so every consumer sees a
    # tz-aware datetime regardless of which branch we exited.
    ts = row["snapshot_iso"] or row["verified_at"]
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            ts = row["verified_at"]
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    # The hourly payload stores chat-ai counts under "chat_ai_counts" or
    # nested under "per_table" depending on the version. Try both for
    # forward/backward compatibility.
    per_table = details.get("chat_ai_counts") or details.get("per_table")
    if not isinstance(per_table, dict):
        return {}, ts
    out: dict[str, int] = {}
    for tbl, val in per_table.items():
        if isinstance(val, dict):
            ca = val.get("chat_ai_count") or val.get("count")
            if isinstance(ca, (int, float)):
                out[tbl] = int(ca)
        elif isinstance(val, (int, float)):
            out[tbl] = int(val)
    return out, ts


async def _v2_row_counts(pool) -> dict[str, int]:
    """Live COUNT(*) per ETL-covered table. Cheap on the indexed tables
    we care about; would not work for billions of rows but our scale
    is fine. Done as a single CTE to halve roundtrip count."""
    rows = await pool.fetch(
        """
        SELECT 'ai_influencers' AS name, COUNT(*)::bigint AS n FROM ai_influencers
        UNION ALL
        SELECT 'conversations',           COUNT(*)::bigint FROM conversations
        UNION ALL
        SELECT 'messages',                COUNT(*)::bigint FROM messages
        """
    )
    return {r["name"]: int(r["n"]) for r in rows}


async def _safe_table_count(pool, table: str) -> int:
    """Defensive COUNT(*) — returns 0 if the table doesn't exist on
    V2 (e.g. migration not applied yet)."""
    exists = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=$1
        )
        """,
        table,
    )
    if not exists:
        return 0
    # asyncpg requires literal table names; safe because `table` here
    # is always a value from V2_NATIVE_TABLES (compile-time constant).
    return int(await pool.fetchval(f"SELECT COUNT(*)::bigint FROM {table}"))


async def _skip_breakdown_by_table(pool) -> dict[str, dict[str, int]]:
    """Total skips per (table, reason) since the beginning. We don't
    time-window this — every deliberate skip is part of the running
    count we need to subtract from the delta."""
    rows = await pool.fetch(
        """
        SELECT table_name, reason, COUNT(*)::bigint AS n
        FROM etl_skipped_rows
        GROUP BY table_name, reason
        """
    )
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["table_name"], {})[r["reason"]] = int(r["n"])
    return out


async def _new_skip_reasons(pool) -> list[str]:
    """Skip reasons present in etl_skipped_rows but NOT in our
    DELIBERATE_SKIP_REASONS allow-list. A new reason = a brand-new
    failure mode that needs Rishi's eye → INVESTIGATE."""
    rows = await pool.fetch("SELECT DISTINCT reason FROM etl_skipped_rows")
    return sorted(
        r["reason"] for r in rows if r["reason"] not in DELIBERATE_SKIP_REASONS
    )


async def _integrity_summary_24h(pool) -> dict:
    """Per-layer pass/fail counts in the last 24h. Used by both the
    report's `integrity_last_24h` field and the verdict logic."""
    rows = await pool.fetch(
        """
        SELECT layer,
               COUNT(*) FILTER (WHERE passed)       AS passes,
               COUNT(*) FILTER (WHERE NOT passed)   AS fails
        FROM etl_integrity_results
        WHERE verified_at > NOW() - INTERVAL '24 hours'
        GROUP BY layer
        """
    )
    out = {
        "tick_layer_passes": 0,
        "tick_layer_fails": 0,
        "hourly_layer_passes": 0,
        "hourly_layer_fails": 0,
        "sample_layer_passes": 0,
        "sample_layer_fails": 0,
        "sentinel_layer_passes": 0,
        "sentinel_layer_fails": 0,
    }
    for r in rows:
        layer = r["layer"]
        out[f"{layer}_layer_passes"] = int(r["passes"] or 0)
        out[f"{layer}_layer_fails"] = int(r["fails"] or 0)
    return out


async def _v2_latest_import_ts(pool) -> datetime | None:
    """When did V2 last successfully ingest an S3 file? Used as the
    `v2_latest_import_ts` field + lag computation.

    `etl_processed_files.processed_at` is `TIMESTAMP` (no tz, migration
    019), so asyncpg returns a tz-naive datetime. Downstream code
    subtracts this from `chat_ai_export_ts` (tz-aware UTC from the
    JSONB payload path) and from `now` (tz-aware UTC). Mixing
    tz-aware + tz-naive raises `TypeError: can't subtract offset-naive
    and offset-aware datetimes` — caught in prod 2026-06-12 when both
    drain and reconciliation 500'd.

    Conceptually `processed_at` is always UTC (the importer writes
    `datetime.now(timezone.utc)` to it), the column just doesn't store
    the offset. Tag the tzinfo on the way out so every consumer sees
    a tz-aware UTC datetime."""
    val = await pool.fetchval(
        """
        SELECT MAX(processed_at) FROM etl_processed_files
        """
    )
    if val is None:
        return None
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(val, datetime) and val.tzinfo is None:
        val = val.replace(tzinfo=timezone.utc)
    return val


def _compute_verdict(
    *,
    tables: list[dict],
    integrity: dict,
    chat_ai_export_ts: datetime | None,
    new_skip_reasons: list[str],
    now: datetime,
    warnings: list[str],
    blocking_issues: list[str],
) -> tuple[str, str]:
    """The verdict-logic-in-one-place. Mutates `warnings` /
    `blocking_issues` lists for the caller to surface."""

    # Any table where chat-ai count is missing → INVESTIGATE first.
    # We can't reason about parity without it.
    missing_evidence = [
        t["name"]
        for t in tables
        if t["kind"] == "etl_covered" and t["chat_ai_count"] is None
    ]
    if missing_evidence:
        blocking_issues.append(
            f"no hourly integrity payload for {missing_evidence} — cannot verify parity"
        )
        return (
            "INVESTIGATE",
            "Missing hourly integrity payload — cannot verify row-count parity. "
            "Either the exporter hasn't emitted an hourly layer yet (run drain), "
            "or the integrity loop hasn't ingested it (check etl_integrity status).",
        )

    # A new (un-allowlisted) skip reason is INVESTIGATE.
    if new_skip_reasons:
        blocking_issues.append(
            f"new skip reasons not in allow-list: {new_skip_reasons}"
        )
        return (
            "INVESTIGATE",
            f"A new skip reason appeared in etl_skipped_rows: {new_skip_reasons}. "
            "Rishi must classify it as deliberate (add to DELIBERATE_SKIP_REASONS) "
            "or fix the underlying data issue.",
        )

    # Any failed integrity check in 24h is INVESTIGATE.
    any_fails = sum(
        integrity.get(f"{layer}_layer_fails", 0) for layer in REQUIRED_LAYERS
    )
    if any_fails > 0:
        blocking_issues.append(f"{any_fails} integrity failure(s) in last 24h")
        return (
            "INVESTIGATE",
            f"{any_fails} integrity verifier failure(s) in the last 24h. "
            "See GET /admin/etl-integrity/details for the row-level diff.",
        )

    # Stale exporter heartbeat → INVESTIGATE.
    if chat_ai_export_ts is not None:
        age = (now - chat_ai_export_ts).total_seconds()
        if age > HEARTBEAT_STALE_SEC:
            blocking_issues.append(
                f"exporter latest payload is {int(age / 60)} min stale"
            )
            return (
                "INVESTIGATE",
                f"chat-ai exporter latest hourly payload is {int(age / 60)} min "
                f"stale (> {int(HEARTBEAT_STALE_SEC / 60)} min). Exporter may be stuck.",
            )

    # Any required layer with 0 passes in 24h → INVESTIGATE.
    cold_layers = [
        layer
        for layer in REQUIRED_LAYERS
        if integrity.get(f"{layer}_layer_passes", 0) == 0
    ]
    if cold_layers:
        blocking_issues.append(f"no recent passes for layers: {cold_layers}")
        return (
            "INVESTIGATE",
            f"No passes in last 24h for integrity layer(s) {cold_layers}. "
            "Run drain to force-emit fresh layer payloads.",
        )

    # Row-count deltas — if any is NOT explained by deliberate skips,
    # the importer probably has rows still in flight → DRAIN_AGAIN.
    unexplained = [
        t["name"]
        for t in tables
        if t["kind"] == "etl_covered" and not t["skips_explain_delta"]
    ]
    if unexplained:
        return (
            "DRAIN_AGAIN",
            f"Row-count delta on {unexplained} not covered by deliberate skips. "
            "Likely in-flight rows; re-trigger drain to catch up.",
        )

    # All checks passed → GREEN.
    skip_total_msg = []
    for t in tables:
        if t["kind"] == "etl_covered" and t["skipped_breakdown"]:
            for reason, n in t["skipped_breakdown"].items():
                skip_total_msg.append(f"{n} {reason} on {t['name']}")
    skips_summary = "; ".join(skip_total_msg) or "no skips"
    return (
        "GREEN",
        f"All {len(ETL_COVERED_TABLES)} ETL tables in row-count parity modulo "
        f"deliberate skips ({skips_summary}); integrity layers "
        f"hourly/sample/sentinel all PASS in last 24h.",
    )
