# ---------------------------------------------------------------------------
# langfuse_middleware.py — LLM trace client lifecycle for the user-memory-service.
#
# ⭐ START HERE: this file exposes three callables:
#   - `init_langfuse()`   — called once at module-load from main.py
#   - `get_langfuse()`    — called per LLM call to get the trace client
#   - `flush_langfuse()`  — called at SIGTERM to drain in-flight traces
#
# IN PHASE 1 (conversation history persistence), this service makes NO
# direct LLM calls — the orchestrator does. This file is included per the
# template standard so future phases (e.g. Phase 2 memory extraction worker)
# can add tracing without restructuring the codebase.
#
# WHAT IS LANGFUSE?
# Langfuse is the self-hosted LLM tracing platform running on rishi-6.
# Every v2 service that makes LLM calls MUST trace to it per CONSTRAINTS D4.
# The trace contains: prompt, response, token counts, latency, cost. This
# gives Rishi a full view of LLM quality + cost across the system.
#
# WHY NO-OP BY DEFAULT?
# `LANGFUSE_TRACING_ENABLED=false` is the docker-compose default. Local dev
# + unit tests run without a real Langfuse instance. Production flips the
# env to "true" via project.config.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
from typing import Optional

try:
    # Langfuse SDK — installed via pyproject.toml's runtime dependencies.
    from langfuse import Langfuse
    _LANGFUSE_AVAILABLE = True
except ImportError:
    # If the SDK ever fails to import, no-op rather than crashing the
    # whole service — tracing is observability, not correctness.
    _LANGFUSE_AVAILABLE = False
    Langfuse = None  # type: ignore[assignment,misc]


# Module-level singleton — None until init_langfuse() is called.
_langfuse: Optional["Langfuse"] = None  # type: ignore[type-arg]


def init_langfuse() -> None:
    """Initialize the Langfuse SDK client singleton.

    WHAT: reads LANGFUSE_TRACING_ENABLED + auth keys from the env; if
          tracing is enabled, constructs the Langfuse client and stores
          it in the module-level `_langfuse` variable.
    WHEN: called once at module-load time from `app/main.py`, the same
          startup sequence as init_sentry().
    WHY:  a module-level singleton avoids reconstructing the client
          per-request (each construction opens an HTTP connection to
          the Langfuse backend).
    """
    global _langfuse

    # Respect the LANGFUSE_TRACING_ENABLED flag — default OFF for local dev.
    enabled = os.environ.get("LANGFUSE_TRACING_ENABLED", "false").lower() == "true"
    if not enabled or not _LANGFUSE_AVAILABLE:
        # No-op. Callers get None from get_langfuse(); they check for None
        # before calling .trace() so this is safe.
        return

    # Langfuse auth pair from Swarm secrets (per D8). Both keys are
    # required; partial configuration → no-op rather than crash.
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://langfuse.rishi.yral.com")

    if not public_key or not secret_key:
        # Keys missing — tracing is configured ON but auth is incomplete.
        # No-op rather than crash; observability should never break the service.
        return

    # Construct the client — this opens a background flush thread inside
    # the Langfuse SDK that batches + ships traces.
    _langfuse = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


def get_langfuse() -> Optional["Langfuse"]:  # type: ignore[type-arg]
    """Return the Langfuse client or None if tracing is disabled.

    WHAT: returns the module-level `_langfuse` singleton. None when
          tracing is disabled or init failed.
    WHEN: called by code paths that make LLM calls (future Phase 2
          memory extraction worker).
    WHY:  central accessor — callers check `if lf := get_langfuse():`
          and trace only when the client is available.
    """
    # Return the singleton. Callers guard with `if lf := get_langfuse()`.
    return _langfuse


def flush_langfuse() -> None:
    """Flush in-flight Langfuse traces before SIGTERM completes.

    WHAT: calls `_langfuse.flush()` to drain the background batch queue
          so traces logged during the final few requests don't disappear.
    WHEN: called from the FastAPI lifespan shutdown hook in `app/main.py`.
    WHY:  SIGTERM (Swarm rolling update / scale-down) kills the process
          shortly after the hook returns. Any unflushed batches are lost.
          Explicit flush ensures seconds of traces land before shutdown.
    """
    # Skip if client was never initialised (tracing disabled / failed).
    if _langfuse is None:
        return

    # Flush the background batch queue — blocks until all in-flight
    # batches are sent to the Langfuse backend or time out.
    _langfuse.flush()


# ===========================================================================
# RELATED FILES:
#   main.py          — calls init_langfuse() at startup + flush_langfuse()
#                      at SIGTERM
#   config.py        — Settings.langfuse_tracing_enabled, public/secret keys
#   secrets.yaml     — LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY declarations
#   pyproject.toml   — langfuse==2.59.7 runtime dependency
# ===========================================================================
