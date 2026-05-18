# ---------------------------------------------------------------------------
# feature_flag.py — FastAPI dependency that gates Day-2 placeholder
# handlers so production cannot serve stubs.
#
# ⭐ START HERE: every Day-2 chat / influencer route takes
# `_: None = Depends(require_day_2_placeholder_flag_enabled)` as the
# FIRST argument. When the flag is OFF (production default), the
# dependency raises HTTPException 503 BEFORE the handler body runs.
# When the flag is ON (local dev + staging), the dependency is a
# no-op and the handler returns its schema-valid stub.
#
# WHY A FASTAPI DEPENDENCY INSTEAD OF AN if-check IN EVERY HANDLER?
# Three wins:
#   1. Single point of definition — flip the gate behavior in one place,
#      not 10 handlers.
#   2. FastAPI applies dependencies BEFORE the handler body runs, so the
#      503 short-circuits without doing any handler work.
#   3. Tests can override the dependency via `app.dependency_overrides`
#      to assert both states without mutating real config (per the
#      pytest fixture in tests/contract/conftest.py).
#
# WHY 503 (service_unavailable), NOT 501 (not_implemented)?
# The contract's error-codes table doesn't include "not_implemented" —
# mobile only knows the 8 codes locked in 00-api-contract.md. From
# mobile's perspective, "Day-4 RPC integration isn't live so the
# endpoint can't serve" IS a service_unavailable condition. Per A8 we
# don't introduce new error codes mid-flight.
#
# WHY THE FLAG NAME IS SO LONG?
# Future-me reading a config-dump file should be able to look at this
# flag and know IMMEDIATELY:
#   - which session is responsible (session_3)
#   - which phase + day this gate exists for (phase_1 day_2)
#   - what the flag does (placeholder_responses)
# A short flag name (e.g. `stub_mode`) would lose all of that and force
# a doc lookup. Per B1 — every name reads as English.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from fastapi import HTTPException, status

from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response
from app.config import get_settings


def require_day_2_placeholder_flag_enabled() -> None:
    """Refuse the request unless the Day-2 stub flag is True.

    WHAT: reads the singleton Settings; if
          enable_session_3_phase_1_day_2_placeholder_responses is False,
          raises HTTPException(503, ApiResponse-shaped body); otherwise
          returns None and the handler proceeds.
    WHEN: applied as a FastAPI dependency on every Day-2 chat +
          influencer handler that returns a SCHEMA-VALID stub. Day-3
          (JWT) + Day-4 (orchestrator RPC) handlers will NOT use this
          dependency since they call real code paths.
    WHY:  prevents an accidental production deploy of a half-built v2
          public-api from serving fake responses to real mobile traffic
          at agent.rishi.yral.com — per the agent definition Day-2 spec
          "MUST NOT serve real mobile traffic until Day-4 RPC integration
          is live."
    """
    # Fetch the cached singleton — cheap call, no env reparse.
    settings = get_settings()

    if not settings.enable_session_3_phase_1_day_2_placeholder_responses:
        # Build a contract-shaped error body so mobile sees an envelope
        # even on the 503, not FastAPI's default `{"detail": "..."}`.
        # FastAPI's HTTPException(detail=<dict>) preserves the dict
        # verbatim in the response body — that's exactly what we want.
        body = error_response(
            "service_unavailable",
            "This endpoint is not yet implemented in this environment. "
            "Enable enable_session_3_phase_1_day_2_placeholder_responses "
            "in local/staging or wait for Day-4 RPC integration.",
        ).model_dump()
        raise HTTPException(
            status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
            detail=body,
        )

    # Flag is on; nothing to do — handler runs next.
    return None


# Re-export to keep imports tidy at handler callsites.
__all__ = ["require_day_2_placeholder_flag_enabled", "status"]


# ===========================================================================
# RELATED FILES:
#   chat_routes.py           — every Day-2 chat handler depends on this
#   influencer_routes.py     — every Day-2 influencer-read handler depends on this
#   health_routes.py         — does NOT depend on this (health probes must
#                              always answer regardless of feature-flag state)
#   ../config.py             — defines the enable_session_3_phase_1_day_2_placeholder_responses field
#   errors.py                — error_response() + HTTP_STATUS_FOR_ERROR_CODE map
#   ../../tests/contract/conftest.py
#                            — overrides this dependency for the test client
# ===========================================================================
