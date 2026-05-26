import logging

import config

logger = logging.getLogger(__name__)

_langfuse = None


def _get_langfuse():
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    if not config.LANGFUSE_SECRET_KEY or not config.LANGFUSE_PUBLIC_KEY:
        return None
    try:
        from langfuse import Langfuse

        _langfuse = Langfuse(
            secret_key=config.LANGFUSE_SECRET_KEY,
            public_key=config.LANGFUSE_PUBLIC_KEY,
            host=config.LANGFUSE_HOST or "https://cloud.langfuse.com",
        )
        logger.info(f"Langfuse initialized (host={config.LANGFUSE_HOST or 'cloud'})")
        return _langfuse
    except Exception as e:
        logger.warning(f"Langfuse init failed (non-fatal): {e}")
        return None


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
    """Record an LLM generation to Langfuse. No-op if Langfuse is not configured."""
    lf = _get_langfuse()
    if lf is None:
        return

    try:
        trace = lf.trace(
            name=trace_name,
            user_id=user_id,
            metadata={
                "conversation_id": conversation_id,
                **(metadata or {}),
            },
        )
        trace.generation(
            name=f"{provider}/{model}",
            model=model,
            input=input_text[:2000],
            output=output_text[:2000],
            usage={
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            metadata={
                "provider": provider,
                "latency_ms": round(latency_ms, 1),
                "is_error": is_error,
            },
            level="ERROR" if is_error else "DEFAULT",
        )
    except Exception as e:
        logger.debug(f"Langfuse trace failed (non-fatal): {e}")


def flush():
    """Flush pending Langfuse events. Call on shutdown."""
    lf = _get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception:
            pass
