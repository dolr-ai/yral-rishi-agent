"""Langfuse LLM tracing via raw HTTP API.

The Langfuse Python SDK v2.60.2 silently fails on self-hosted instances.
This module uses the raw /api/public/ingestion endpoint instead, which
we've verified works (status 207, successes=[{status: 201}]).
"""

import base64
import logging
import uuid
from datetime import datetime, timezone

import httpx

import config

logger = logging.getLogger(__name__)

_auth_header: str | None = None


def _get_auth() -> str | None:
    global _auth_header
    if _auth_header is not None:
        return _auth_header
    if not config.LANGFUSE_SECRET_KEY or not config.LANGFUSE_PUBLIC_KEY:
        return None
    creds = f"{config.LANGFUSE_PUBLIC_KEY}:{config.LANGFUSE_SECRET_KEY}"
    _auth_header = "Basic " + base64.b64encode(creds.encode()).decode()
    logger.info(f"Langfuse tracing enabled (host={config.LANGFUSE_HOST})")
    return _auth_header


def trace_generation(
    *,
    trace_name: str,
    user_id: str | None = None,
    model: str,
    provider: str,
    input_text: str,
    output_text: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: float = 0,
    metadata: dict | None = None,
    is_error: bool = False,
    conversation_id: str | None = None,
):
    auth = _get_auth()
    if auth is None:
        return

    host = config.LANGFUSE_HOST or "https://cloud.langfuse.com"
    now = datetime.now(timezone.utc).isoformat()
    trace_id = str(uuid.uuid4())
    gen_id = str(uuid.uuid4())

    # 21γ.P26 fix (2026-06-16): propagate input/output onto the trace
    # body so the Langfuse UI's trace-summary rollup ("user message →
    # AI reply") populates, not just the child generation. Pre-fix the
    # generation carried full data while the trace summary showed
    # "Looks like this trace didn't receive an input or output." Same
    # 2000-char cap as the generation so a single chat turn can't
    # double-bill the Langfuse payload. Truncation policy matches
    # generation-side intentionally — both fields render the same
    # snippet in the UI; mismatched truncation would show different
    # text at the two levels and confuse triage.
    # sessionId groups traces by chat in the Langfuse Sessions tab so a
    # full user↔bot conversation reads as one thread. Conditional on
    # conversation_id so non-chat traces (e.g. background generations)
    # stay session-less and don't pollute the tab.
    trace_body: dict = {
        "id": trace_id,
        "name": trace_name,
        "userId": user_id,
        "input": input_text[:2000],
        "output": output_text[:2000],
        "metadata": {
            "conversation_id": conversation_id,
            **(metadata or {}),
        },
    }
    if conversation_id:
        trace_body["sessionId"] = conversation_id

    batch = [
        {
            "id": trace_id,
            "type": "trace-create",
            "timestamp": now,
            "body": trace_body,
        },
        {
            "id": gen_id,
            "type": "generation-create",
            "timestamp": now,
            "body": {
                "id": gen_id,
                "traceId": trace_id,
                "name": f"{provider}/{model}",
                "model": model,
                "input": input_text[:2000],
                "output": output_text[:2000],
                "usage": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                },
                "metadata": {
                    "provider": provider,
                    "latency_ms": round(latency_ms, 1),
                    "is_error": is_error,
                },
                "level": "ERROR" if is_error else "DEFAULT",
            },
        },
    ]

    try:
        resp = httpx.post(
            f"{host}/api/public/ingestion",
            json={"batch": batch},
            headers={"Authorization": auth, "Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code not in (200, 207):
            logger.debug(f"Langfuse ingestion returned {resp.status_code}")
    except Exception as e:
        logger.debug(f"Langfuse trace failed (non-fatal): {e}")


def flush():
    pass
