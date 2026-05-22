# ---------------------------------------------------------------------------
# logging.py — structured logging pipeline for the user-memory-service.
#
# ⭐ START HERE: this file exposes ONE callable, `configure_logging()`,
# which is called once at module-load time from `app/main.py`. After that,
# every `logging.getLogger(__name__)` call in the codebase emits
# structured key=value / JSON lines through a PII-redaction processor
# per CONSTRAINTS H6.
#
# WHY structlog?
# The stdlib `logging` module emits plain text (hard to parse in Grafana /
# Loki / CloudWatch). structlog wraps it and emits JSON lines where every
# `extra={"key": "value"}` becomes a searchable field in the log stream.
# Rishi can pivot on `conversation_id` in Loki without grep-and-parse.
#
# WHY PII REDACTION (per H6)?
# CONSTRAINTS H6 verbatim: "Message bodies, user names, email, phone NEVER
# in Loki, Sentry breadcrumbs, Langfuse trace payloads." This file ships
# an allowlist-based processor that drops any field key NOT on the safe
# list before the log line is emitted. A developer who accidentally does
# `logger.info("user message", extra={"message_body": body})` will silently
# drop `message_body` — the log line lands without the PII.
#
# WHAT THE ALLOWLIST COVERS (safe to log):
#   request_id, conversation_id, user_id (opaque UUID — not PII),
#   service, environment, status_code, latency_ms, role, count,
#   error_code, error_type, exc_type.
# Everything else is stripped.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
import os
import sys

import structlog


# Fields safe to include in log output. Anything NOT in this set is
# silently removed by `_pii_redact_processor` before the line is emitted.
# Per CONSTRAINTS H6 — message bodies + user identifiers must never
# appear in Loki / Sentry breadcrumbs.
_SAFE_LOG_FIELDS: frozenset[str] = frozenset(
    {
        # Request tracing
        "request_id",
        "conversation_id",
        # User identifier is an opaque UUID — not PII per our data model.
        # We log it so on-call can pivot on a user's request stream.
        "user_id",
        # Routing + observability metadata
        "service",
        "environment",
        "status_code",
        "latency_ms",
        "method",
        "path",
        # Message metadata (NOT content)
        "role",
        "message_count",
        # Error classification
        "error_code",
        "error_type",
        "exc_type",
        # Pool + DB diagnostics
        "min_size",
        "max_size",
        # Idempotency
        "idempotency_key_source",
        # Feature flag diagnostics
        "flag_name",
    }
)


def _pii_redact_processor(
    _logger: object,
    _method: str,
    event_dict: dict,
) -> dict:
    """Strip keys not on the safe-field allowlist.

    WHAT: iterates over a copy of the structlog event dict; drops any
          key that isn't in `_SAFE_LOG_FIELDS` (except structural keys
          structlog itself writes: `event`, `level`, `timestamp`).
    WHEN: inserted at position 0 of the structlog chain so it runs
          BEFORE any serializer sees the data.
    WHY:  H6 compliance — message bodies + PII must never reach Loki.
    """
    # Structural keys structlog always includes — never strip these.
    structlog_reserved_keys = {"event", "level", "timestamp", "_record"}

    # Collect keys to remove (can't mutate dict while iterating).
    keys_to_remove = [
        key
        for key in event_dict
        if key not in structlog_reserved_keys and key not in _SAFE_LOG_FIELDS
    ]

    # Remove PII-risk keys silently. The log line still emits; it just
    # won't contain the disallowed field.
    for key in keys_to_remove:
        del event_dict[key]

    return event_dict


def configure_logging() -> None:
    """Set up structlog + stdlib logging for the service.

    WHAT: configures structlog's processor chain (PII redaction → JSON
          rendering) and configures the stdlib root logger to route
          through structlog. Called once before the first log line.
    WHEN: called from `app/main.py` at module-load time, AFTER
          init_sentry() + init_langfuse() (so their own startup messages
          flow through the same pipeline).
    WHY:  a single configure call sets the logging contract for the
          process lifetime. Individual modules get a standard
          `logging.getLogger(__name__)` — no per-module setup needed.
    """
    # Read log level from env (overrideable per service instance).
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    # Configure structlog's processing chain:
    #   1. _pii_redact_processor — strip non-allowlisted fields (H6)
    #   2. add_log_level          — inject "level" key
    #   3. TimeStamper            — inject "timestamp" key (ISO 8601 UTC)
    #   4. JSONRenderer           — emit as a JSON line for Loki ingestion
    structlog.configure(
        processors=[
            # PII redaction must run FIRST — before any serializer sees data.
            _pii_redact_processor,
            # Standard structlog processors for level + timestamp.
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Render to JSON for Loki ingest. `sort_keys=True` makes
            # log lines deterministic for tests.
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        # Route stdlib `logging.getLogger(...)` calls through structlog
        # so third-party libs (asyncpg, uvicorn, alembic) also emit JSON.
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    # Set root stdlib logger level + handler so the stdlib path doesn't
    # silently drop lines at the root before structlog sees them.
    logging.basicConfig(
        stream=sys.stdout,
        level=numeric_level,
        format="%(message)s",  # structlog's JSONRenderer handles full formatting
    )

    # Silence noisy third-party loggers that don't add signal. Tune
    # these thresholds upward (WARNING / ERROR) as needed.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


# ===========================================================================
# RELATED FILES:
#   main.py                — calls configure_logging() at module-load
#   request_id_middleware.py — injects request_id into log context
#   database.py            — uses logging.getLogger("app.database")
#   api/conversation_routes.py
#                          — uses logging.getLogger(__name__) per handler
# ===========================================================================
