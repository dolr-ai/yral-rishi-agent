# ---------------------------------------------------------------------------
# sentry_middleware.py — wires the running service into Sentry error tracking.
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
# before the FastAPI app object is built, or exceptions raised during the
# app's own startup (e.g. asyncpg pool init failing) won't be captured.
# Lifespan startup runs AFTER the app exists — too late.
#
# WHY SENTRY HOST = sentry.rishi.yral.com?
# Per CONSTRAINTS A7 (reinforced 3 times by Rishi): every v2 service ships
# errors to Rishi's self-hosted Sentry on rishi-3, NEVER the team-shared
# apm.yral.com. The DSN itself (a secret) encodes the host; we just trust
# it to be the right one and let validate-secrets.sh (Day 3) check.
#
# WHAT HAPPENS WHEN SENTRY_DSN IS EMPTY?
# We no-op. Local dev runs without a real Sentry project so errors don't
# pollute prod's volume. Production deploys always have the DSN injected
# from the Swarm secret.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os

import sentry_sdk


def init_sentry() -> None:
    """Initialize Sentry error tracking for this service.

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

    # Empty DSN → don't init. This is the local-dev path AND the safety
    # net for any environment where the secret wasn't wired up. The
    # service runs fine; we just don't report errors anywhere.
    if not dsn:
        return

    # Service-name tag stamped on every Sentry event so the Sentry UI
    # can group + filter by service per D3. Sourced from project.config
    # via the SENTRY_SERVICE_TAG env var; the placeholder default lets
    # us spot any service that forgot to wire it.
    service = os.environ.get("SENTRY_SERVICE_TAG", "unknown-service")

    # Environment tag — one of: local | staging | production. Set in
    # docker-compose.yml (local) or by the deploy workflow (staging/prod).
    environment = os.environ.get("ENVIRONMENT", "local")

    # Initialize the SDK. The FastAPI + Starlette integrations auto-load
    # because sentry-sdk[fastapi] is installed; we don't pass them
    # explicitly. traces_sample_rate=0.1 starts conservative — tunable
    # via shared-config.yaml later if we need higher fidelity for E1.
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1,
        # send_default_pii=False per H6 — request bodies + headers
        # never go to Sentry; only stack traces + structured tags.
        send_default_pii=False,
    )

    # Stamp the service tag globally so every event the SDK captures
    # inherits it (per D3 — every v2 service tagged `service=<name>`).
    sentry_sdk.set_tag("service", service)


# ===========================================================================
# RELATED FILES:
#   main.py                — calls init_sentry() at module import time
#   pyproject.toml         — declares sentry-sdk[fastapi] dependency
#   shared-config.yaml     — host = sentry.rishi.yral.com (per A7 + C4)
#   secrets.yaml.template  — SENTRY_DSN entry (per D8)
#   project.config         — SENTRY_SERVICE_TAG = <service-name>
# ===========================================================================
