# ---------------------------------------------------------------------------
# sentry_middleware.py — wires the user-memory-service into Sentry error tracking.
#
# ⭐ START HERE: this file does ONE thing — `init_sentry()` is called once
# at module-load time from `app/main.py` BEFORE the FastAPI app is created.
# After that, the sentry-sdk library auto-captures every unhandled exception
# in the request path and ships it to sentry.rishi.yral.com tagged with the
# service name (per CONSTRAINTS A7 + D3).
#
# WHY INIT AT MODULE-LOAD, NOT IN A LIFESPAN HOOK?
# Sentry's FastAPI integration hooks into Starlette's exception-handling
# machinery at the time of `sentry_sdk.init()`. The hook MUST be in place
# before the FastAPI app object is built, or exceptions raised during
# app startup (e.g. asyncpg pool init failing) won't be captured.
# Lifespan startup runs AFTER the app exists — too late.
#
# WHY SENTRY HOST = sentry.rishi.yral.com?
# Per CONSTRAINTS A7 (reinforced 3 times by Rishi): every v2 service ships
# errors to Rishi's self-hosted Sentry on rishi-3, NEVER the team-shared
# apm.yral.com. The DSN encodes the host; `validate-secrets.sh` checks it.
#
# WHAT HAPPENS WHEN SENTRY_DSN IS EMPTY?
# We no-op. Local dev runs without a real Sentry project so errors don't
# pollute production event volume. Production deploys always have the
# DSN injected from the Swarm secret.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os

import sentry_sdk


def init_sentry() -> None:
    """Initialize Sentry error tracking for the user-memory-service.

    WHAT: configures the global sentry-sdk to ship errors to
          sentry.rishi.yral.com (per A7) tagged with the service name.
    WHEN: called exactly once at module-load time from `app/main.py`,
          BEFORE the FastAPI app object is constructed.
    WHY:  Sentry's FastAPI integration must be in place before app
          startup so exceptions during startup (DB init, etc.) are
          captured. Module-load is the only place that beats startup.
    """
    # Read the DSN from the environment. Production gets it from a Swarm
    # secret mounted as an env var by the deploy workflow; local dev
    # leaves it empty so the SDK no-ops.
    dsn = os.environ.get("SENTRY_DSN", "")

    # Empty DSN → no-op. This is the local-dev path AND the safety net
    # for any environment where the secret wasn't wired up.
    if not dsn:
        return

    # Service-name tag per D3 — every Sentry event is filterable by
    # service in the Sentry UI. Sourced from project.config's
    # SENTRY_SERVICE_TAG; defaults to the service name if missing.
    service_tag = os.environ.get("SENTRY_SERVICE_TAG", "yral-rishi-agent-user-memory-service")

    # Fraction of transactions to record for performance profiling. 0.1
    # = 10% — conservative default per CONSTRAINTS E1 (latency-gated).
    # Tune upward once the service is live and we have real baseline data.
    try:
        traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    except ValueError:
        # Non-numeric env var → fall back to 10% rather than crashing.
        traces_sample_rate = 0.1

    # Initialize the Sentry SDK. The `fastapi` integration auto-adds the
    # Starlette middleware that captures unhandled exceptions. The
    # `tags` dict stamps every event with the service name per D3.
    sentry_sdk.init(
        dsn=dsn,
        integrations=[sentry_sdk.integrations.fastapi.FastApiIntegration()],
        traces_sample_rate=traces_sample_rate,
        environment=os.environ.get("ENVIRONMENT", "local"),
        release=os.environ.get("IMAGE_TAG", "dev"),
        # Stamp every event with the service tag so the Sentry UI can
        # pivot on "which service sent this?" per D3.
        default_integrations=True,
    )
    sentry_sdk.set_tag("service", service_tag)


# ===========================================================================
# RELATED FILES:
#   main.py          — calls init_sentry() before building the FastAPI app
#   config.py        — Settings.sentry_dsn + sentry_service_tag fields
#   secrets.yaml     — SENTRY_DSN secret declaration per D8
#   project.config   — SENTRY_SERVICE_TAG value used at deploy time
# ===========================================================================
