# ---------------------------------------------------------------------------
# observability.py — emit the JWT shadow-validate divergence metric.
#
# ⭐ START HERE: one function, `emit_dual_validate_result()`. Called by
# the dependency after both validators run. Emits to:
#   - Sentry: a breadcrumb on every call + a captured warning event on
#     each divergence (so Sentry alerts can fire on divergence rate).
#   - Langfuse: a trace event with `jwt.shadow.strict.{ok,reason,
#     divergence_vs_legacy}` metadata per Rishi's Day-3 directive.
#
# WHY ALL EMISSIONS GO THROUGH ONE FUNCTION?
# Centralized so the Sentry alert config (deferred to Sentry UI / IaC)
# can target ONE event name + one tag schema instead of chasing the
# emission shape across the codebase. When Rishi later asks "what's the
# divergence rate right now," there's one query to answer it.
#
# WHY SENTRY + LANGFUSE, NOT JUST ONE?
# Sentry is the alerting engine (per A7 + D3) — it has aggregations,
# alert rules, on-call paging. Langfuse is the trace store (per D4) —
# it's where you correlate ONE specific user's request through the
# whole system. Divergence-rate-over-time goes to Sentry; "WHY did THIS
# specific user's token diverge?" goes to Langfuse.
#
# WHY THE WARN LEVEL (NOT ERROR)?
# Divergence DURING SHADOW MODE is expected — that's literally why
# shadow mode exists, to surface the cases legacy accepts but strict
# would reject. ERROR-level would saturate the on-call queue. WARN
# level is the "this is a signal, not a crisis" tier; alert rules
# aggregate warns into a single page when rate > 1%/hr (config in
# Sentry UI).
#
# WHY NO-OP WHEN SENTRY / LANGFUSE NOT CONFIGURED?
# Local dev runs with empty SENTRY_DSN + LANGFUSE_TRACING_ENABLED=false.
# Both SDKs already no-op cleanly when not configured; this module
# defers to that built-in behavior rather than adding its own
# is-enabled checks.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Optional

import sentry_sdk

from app.api.auth.validators import ValidationResult
from app.langfuse_middleware import get_langfuse


def emit_dual_validate_result(
    legacy: ValidationResult,
    strict: ValidationResult,
    request_path: Optional[str] = None,
) -> bool:
    """Log both validator results + return whether they diverged.

    WHAT: emits a Sentry breadcrumb on every call, a captured warning on
          each divergence, and a Langfuse trace event with the locked
          metadata schema. Returns True if legacy.ok != strict.ok.
    WHEN: called by the auth dependency once per request, after both
          validators have returned.
    WHY:  centralized emission so the Sentry alert config + Langfuse
          dashboard config can target ONE event name / metadata schema.
    """
    divergence = legacy.ok != strict.ok

    # The metadata schema Rishi's Day-3 directive locked in. Used by
    # both Sentry tags AND Langfuse trace metadata so the dashboards
    # query the same key names.
    metadata = {
        "jwt.shadow.legacy.ok": legacy.ok,
        "jwt.shadow.legacy.reason": legacy.reason,
        "jwt.shadow.strict.ok": strict.ok,
        "jwt.shadow.strict.reason": strict.reason,
        "jwt.shadow.divergence_vs_legacy": divergence,
        # Path included so the divergence histogram can pivot by route
        # — e.g., is influencer-list more often divergent than chat-send?
        "jwt.shadow.request_path": request_path or "unknown",
    }

    # Sentry breadcrumb on every call so the FULL context is attached
    # to whatever later event Sentry captures from this request.
    sentry_sdk.add_breadcrumb(
        category="jwt.shadow",
        level="info",
        message="dual_validate",
        data=metadata,
    )

    # On divergence: capture a Sentry event at WARN level. Alert rules
    # (configured in Sentry UI per the deferred config track) aggregate
    # these into one alert when rate > 1%/hr per Rishi's Day-3 directive.
    # Tags (vs data) appear in Sentry's filter sidebar so the
    # divergence-reason histogram is a 2-click pivot. `new_scope()` is
    # the sentry-sdk v2 replacement for the deprecated `push_scope()`.
    if divergence:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("jwt.shadow.divergence", "true")
            scope.set_tag("jwt.shadow.legacy_reason", legacy.reason)
            scope.set_tag("jwt.shadow.strict_reason", strict.reason)
            scope.set_tag("jwt.shadow.request_path", request_path or "unknown")
            scope.set_context("jwt_shadow_validation", metadata)
            sentry_sdk.capture_message(
                "JWT shadow validation divergence (legacy vs strict)",
                level="warning",
            )

    # Langfuse trace event so per-request divergence is correlatable.
    # The langfuse client no-ops when LANGFUSE_TRACING_ENABLED is false.
    langfuse_client = get_langfuse()
    if langfuse_client is not None:
        try:
            # `event()` creates a standalone trace event — does NOT
            # require an open trace context, so it works even before
            # Day-4 wires per-request langfuse traces.
            langfuse_client.event(
                name="jwt.shadow.dual_validate",
                metadata=metadata,
                # `level` is a langfuse-side severity tag; WARNING when
                # divergent so the langfuse query can filter by it.
                level="WARNING" if divergence else "DEFAULT",
            )
        except Exception as exc:  # noqa: BLE001 — never let observability crash auth
            # If langfuse emission fails (network blip, SDK bug, etc.)
            # we MUST NOT block authentication. Drop a Sentry breadcrumb
            # for visibility + carry on.
            sentry_sdk.add_breadcrumb(
                category="jwt.shadow",
                level="warning",
                message=f"langfuse emit failed: {exc}",
            )

    return divergence


# ===========================================================================
# RELATED FILES:
#   dependency.py            — calls emit_dual_validate_result() per request
#   validators.py            — defines ValidationResult + the reason strings
#   ../../sentry_middleware.py    — the init_sentry() that wires the SDK
#   ../../langfuse_middleware.py  — get_langfuse() returns the singleton
# ===========================================================================
