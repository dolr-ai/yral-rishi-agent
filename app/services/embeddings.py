"""Gemini text-embedding-004 — 768-dim embeddings for memory semantic search.

Same API key as chat (GEMINI_API_KEY), separate model endpoint. Caller is
responsible for batching — this module exposes single-text and batch-text
entry points. Returns Python lists; asyncpg accepts them when the column
type is vector(768).
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)

# Gemini retired text-embedding-004 between when the Phase 4.4 PR landed and
# rollout — verified empirically (`/v1beta/models/text-embedding-004:embedContent`
# returns 404, and `/v1beta/models` no longer lists it). The current stable
# embedding model is gemini-embedding-001, which defaults to 3072 dimensions
# but supports Matryoshka truncation via outputDimensionality. We pin 768 to
# match the user_memories.embedding column type — switching to 3072 would
# require a schema migration and reindex.
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"
EMBEDDING_TIMEOUT = 10.0


async def embed_text(text: str) -> list[float] | None:
    """Return a 768-dim embedding for the given text, or None on failure.

    Failure is non-fatal — the caller (memory upsert / hot-path search)
    must degrade gracefully when embedding is unavailable.
    """
    if not config.GEMINI_API_KEY:
        return None
    text = (text or "").strip()
    if not text:
        return None

    url = f"{GEMINI_NATIVE_URL}/models/{EMBEDDING_MODEL}:embedContent"
    payload = {
        "model": f"models/{EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": EMBEDDING_DIM,
    }
    try:
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as http:
            response = await http.post(
                url,
                json=payload,
                params={"key": config.GEMINI_API_KEY},
                timeout=EMBEDDING_TIMEOUT,
            )
            response.raise_for_status()
        data = response.json()
        values = (data.get("embedding") or {}).get("values")
        if not values or len(values) != EMBEDDING_DIM:
            logger.warning(
                f"embed_text: unexpected response shape (len={len(values) if values else 0})"
            )
            return None
        return values
    except Exception as e:
        logger.warning(f"embed_text failed (non-fatal): {e}")
        return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed a batch of texts.

    gemini-embedding-001 does NOT support synchronous :batchEmbedContents
    (only the long-running asyncBatchEmbedContent op, which is overkill for
    our backfill scale of <100k rows). We fan out single-text requests in
    parallel — Gemini's quota is per-RPM, so concurrent calls are fine.

    Returns a list parallel to the input. Failed items get None.
    """
    if not config.GEMINI_API_KEY or not texts:
        return [None] * len(texts)
    import asyncio

    results = await asyncio.gather(*(embed_text(t) for t in texts))
    return list(results)


def memory_to_embed_text(category: str, key: str, value: str) -> str:
    """Format a memory for embedding. Keep close to how it'll be queried —
    the user's chat message is a question/statement, so we frame memories
    as natural-language facts."""
    return f"{category}: {key} = {value}"
