# ---------------------------------------------------------------------------
# logging.py — structured logging with PII allowlist redaction (per H6).
#
# ⭐ START HERE: `configure_logging()` is called once at module-load from
# `app/main.py`. After that, anywhere can do:
#
#     import structlog
#     logger = structlog.get_logger()
#     logger.info("user_action", user_id_hash="abc", action="click")
#
# The line lands as JSON (production) or pretty-console (local dev),
# carries the current request's `request_id` automatically, and REDACTS
# any field key not on `_FIELD_ALLOWLIST` below — per CONSTRAINTS H6.
#
# WHY ALLOWLIST INSTEAD OF DENYLIST?
# H6: "Structured logger with allow-list of safe fields." A denylist
# misses any new field name nobody flagged; an allowlist defaults to
# safe. To add a new safe field name, edit `_FIELD_ALLOWLIST` here
# (1-line PR — tiny security review).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
import os
import sys

import structlog

from app.request_id_middleware import get_request_id


# Field keys safe to log in plain text. Anything else gets redacted.
# Extend with care; each addition is a small security review.
_FIELD_ALLOWLIST: frozenset[str] = frozenset({
    "event", "level", "timestamp", "logger",        # structlog built-ins
    "request_id", "idempotency_key",                # request-scoped IDs
    "service", "environment",                       # service identity
    "method", "path", "status_code", "duration_ms", # HTTP shape (no body)
    "user_id_hash", "influencer_id", "conversation_id",  # opaque user IDs
    "error_type", "exc_info",                       # error classification
    "model", "provider", "input_tokens", "output_tokens",  # LLM telemetry
})


def _redact_disallowed_fields(_logger, _method_name, event_dict: dict) -> dict:
    """structlog processor: redact field values whose key is not on the allowlist.

    WHAT: replaces values of unsafe-keyed fields with "<redacted>"; keys
          stay so log shape is still inspectable.
    WHEN: runs on every log call, before serialization.
    WHY:  H6 — PII never reaches Loki / Sentry / Langfuse via logs.
    """
    for key in list(event_dict.keys()):
        if key not in _FIELD_ALLOWLIST:
            event_dict[key] = "<redacted>"
    return event_dict


def _inject_request_id(_logger, _method_name, event_dict: dict) -> dict:
    """structlog processor: stamp the current request's ID on every log line."""
    event_dict["request_id"] = get_request_id()
    return event_dict


def configure_logging() -> None:
    """Configure structlog + stdlib logging once at module-load.

    WHAT: wires the request_id injector + allowlist redactor + JSON
          renderer (production) / pretty-console renderer (local dev).
    WHEN: called once at module-load from `app/main.py`. Mirrors
          init_sentry + init_langfuse for consistency.
    WHY:  one consistent log format across all 13 v2 services makes
          Loki queries portable; the redactor enforces H6 globally.
    """
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = logging.getLevelNamesMapping().get(log_level_name, logging.INFO)

    # Pretty-print locally so the dev's terminal stays readable;
    # JSON in staging/production so Loki + Grafana can parse fields.
    is_local = os.environ.get("ENVIRONMENT", "local").lower() == "local"
    final_renderer = (
        structlog.dev.ConsoleRenderer() if is_local
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        # ORDER MATTERS: request_id injector adds an allowlisted key
        # FIRST; redactor scrubs anything not on the allowlist NEXT;
        # standard structlog bookkeeping after; renderer last.
        processors=[
            _inject_request_id,
            _redact_disallowed_fields,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            final_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    # Pin stdlib logging to the same threshold so libraries that DON'T
    # use structlog (asyncpg, httpx, etc.) emit at the same level.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)


# ===========================================================================
# RELATED FILES:
#   main.py                  — calls configure_logging() at module-load
#   request_id_middleware.py — supplies get_request_id() for the injector
#   pyproject.toml           — declares the structlog dependency
# ===========================================================================
