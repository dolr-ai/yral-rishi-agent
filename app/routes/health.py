from fastapi import APIRouter, HTTPException, Request

import database
import config

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "status": "running",
    }


@router.get("/health/live")
async def health_live():
    return {"status": "OK"}


@router.get("/health")
async def health():
    if not await database.check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"status": "ERROR", "database": "unreachable"},
        )
    return {"status": "OK", "database": "reachable"}


@router.get("/status")
async def status():
    db_healthy = await database.check_db_health()
    return {
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "environment": config.ENVIRONMENT,
        "database": "reachable" if db_healthy else "unreachable",
        "gemini_model": config.GEMINI_MODEL,
    }


@router.get("/admin/etl-status")
async def etl_status(request: Request):
    """Operator view of the chat-ai → v2 ETL cursor.

    JWT-gated: any authenticated caller can read it. The `last_error` field
    can include partial connection-string fragments and host names from
    asyncpg's error messages — we don't want to expose those to random
    scanners.
    """
    from auth import get_current_user
    from services.etl_chat_ai import get_status
    from datetime import datetime

    # Raises 401 if no/bad JWT
    get_current_user(request)

    pool = await database.get_pool()
    raw = await get_status(pool)
    # Serialize timestamps for JSON
    for t in raw["tables"]:
        for k in ("last_sync_ts", "last_run_at"):
            v = t.get(k)
            if isinstance(v, datetime):
                t[k] = v.isoformat()
    return raw


def _iso_serialize(obj):
    """Recursively turn datetimes into ISO strings so the FastAPI JSON
    encoder doesn't choke on the nested asyncpg Record values."""
    from datetime import datetime

    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _iso_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_iso_serialize(v) for v in obj]
    return obj


@router.get("/admin/etl-integrity")
async def etl_integrity(request: Request):
    """Summary: latest result per layer (tick/hourly/sample/sentinel) +
    24h pass/fail counts. JWT-gated."""
    from auth import get_current_user
    from services.etl_integrity import get_status

    get_current_user(request)
    pool = await database.get_pool()
    raw = await get_status(pool)
    return _iso_serialize(raw)


@router.get("/admin/etl-integrity/details")
async def etl_integrity_details(request: Request, layer: str, hours: int = 24):
    """Drill-in: every result for the given layer in the last N hours.

    `layer` must be one of tick/hourly/sample/sentinel. `hours` clamped
    to [1, 168] to bound response size."""
    from auth import get_current_user
    from services.etl_integrity import get_details

    get_current_user(request)
    valid = {"tick", "hourly", "sample", "sentinel"}
    if layer not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"layer must be one of {sorted(valid)}",
        )
    hours = max(1, min(168, hours))
    pool = await database.get_pool()
    raw = await get_details(pool, layer, hours)
    return _iso_serialize(raw)


@router.get("/admin/etl-integrity/stale")
async def etl_integrity_stale(request: Request):
    """How stale is V2 vs. chat-ai's latest message? Reads the last
    passing sentinel result + V2's MAX(messages.created_at)."""
    from auth import get_current_user
    from services.etl_integrity import get_staleness

    get_current_user(request)
    pool = await database.get_pool()
    raw = await get_staleness(pool)
    return _iso_serialize(raw)


@router.get("/admin/etl-skipped")
async def etl_skipped(request: Request, hours: int = 24, reason: str | None = None):
    """Recent etl_skipped_rows entries — Option A audit trail.

    `reason` must be one of conflict/orphan if provided. `hours` clamped
    to [1, 168]. Capped at 500 rows in the response."""
    from auth import get_current_user
    from services.etl_chat_ai import get_skipped

    get_current_user(request)
    if reason is not None and reason not in {"conflict", "orphan"}:
        raise HTTPException(
            status_code=400,
            detail="reason must be 'conflict' or 'orphan'",
        )
    hours = max(1, min(168, hours))
    pool = await database.get_pool()
    raw = await get_skipped(pool, hours, reason)
    return _iso_serialize(raw)
