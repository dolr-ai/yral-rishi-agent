# ---------------------------------------------------------------------------
# langfuse_middleware.py — wires the running service into Langfuse tracing.
#
# ⭐ START HERE: this file exposes THREE things:
#   1. `init_langfuse()` — called once at module-load from `app/main.py`.
#   2. `get_langfuse()`  — returns the live client (or None if disabled).
#   3. `flush_langfuse()` — drains pending traces; called from lifespan
#                            shutdown so SIGTERM doesn't drop in-flight traces.
#
# WHAT LANGFUSE DOES FOR US
# Per CONSTRAINTS D4: every LLM call from every v2 service traces to the
# self-hosted Langfuse on rishi-6. Each trace records prompt + response +
# tokens + latency + cost, joinable to Sentry + Prometheus via the request
# correlation ID (added in PR 3). This is the "what did the model say and
# how long did it take" backbone for E1 (50%-faster goal) and H8 (eval
# harness).
#
# WHY THE init/get/flush TRIO INSTEAD OF auto-magic?
# The Langfuse SDK is a client, not a hooked-in middleware. It only does
# work when consumer code (the LLM client added later, per A10) calls
# `client.trace(...)`. We expose `get_langfuse()` so those consumers can
# fetch the singleton, and `flush_langfuse()` so the lifespan shutdown
# can ensure no traces are lost when the process exits.
#
# WHAT HAPPENS WHEN LANGFUSE_TRACING_ENABLED IS FALSE?
# We no-op everywhere — no client is constructed, `get_langfuse()` returns
# None, `flush_langfuse()` is a no-op. Local dev runs with this off by
# default (per the .env.example we ship) so the heavy ~1 GB Langfuse stack
# isn't required on every laptop.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os

from langfuse import Langfuse


# Module-level singleton. None until init_langfuse() runs; stays None
# when tracing is disabled. Other modules access via get_langfuse() —
# they should NOT import _client directly (the underscore signals
# "private — go through the getter").
_client: Langfuse | None = None


def init_langfuse() -> None:
    """Initialize the module-level Langfuse client.

    WHAT: constructs a singleton `Langfuse` client pointed at
          langfuse.rishi.yral.com (per D4) and stashes it in
          module-level `_client`.
    WHEN: called exactly once at module-load time from `app/main.py`.
          Matches the Sentry init pattern for consistency.
    WHY:  centralizing init means the LLM client (added per A10) can
          fetch the same singleton via `get_langfuse()` without
          re-reading env vars or duplicating auth logic.
    """
    global _client

    # Feature-flag the whole module via the same env var docker-compose.yml
    # uses locally (set to "false") and production CI flips to "true".
    # Anything other than the literal "true" string disables tracing —
    # default-deny so a typo doesn't accidentally ship traces.
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "false").lower() != "true":
        return

    # Auth pair from the Swarm secrets (per D8). Missing either half ==
    # treat as disabled rather than crash — keeps the service runnable
    # in a half-configured environment while still being verbose enough
    # in the logs to spot the misconfiguration.
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        return

    # Host from shared-config.yaml's langfuse.host value, propagated to
    # the env by the deploy workflow. Default points at the production
    # rishi-6 instance so a forgotten env var still routes correctly.
    host = os.environ.get("LANGFUSE_HOST", "https://langfuse.rishi.yral.com")

    _client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


def get_langfuse() -> Langfuse | None:
    """Return the singleton Langfuse client, or None if disabled.

    WHAT: hands out the module-level client other code uses to trace.
    WHEN: called whenever code needs to record a trace (most commonly
          from the LLM client added in a later PR per A10).
    WHY:  prevents callers from re-init'ing or duplicating the client;
          None-return lets callers handle the disabled case cleanly
          without try/except guards.
    """
    return _client


def flush_langfuse() -> None:
    """Drain any pending traces before the process exits.

    WHAT: blocks until the Langfuse SDK's in-memory queue is shipped
          to the server. No-op when the client wasn't initialized.
    WHEN: called from `app/main.py`'s lifespan shutdown so SIGTERM
          (Swarm rolling update, scale-down) doesn't lose traces.
    WHY:  the SDK batches traces in memory + ships them on a timer;
          an unflushed exit can drop seconds-worth of in-flight data
          right when we most want to see what was happening.
    """
    if _client is not None:
        _client.flush()


# ===========================================================================
# RELATED FILES:
#   main.py                — calls init_langfuse() and flush_langfuse()
#   pyproject.toml         — declares the langfuse dependency
#   shared-config.yaml     — langfuse.host = langfuse.rishi.yral.com (D4)
#   secrets.yaml.template  — LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY (D8)
#   docker-compose.yml     — sets LANGFUSE_TRACING_ENABLED=false locally
# ===========================================================================
