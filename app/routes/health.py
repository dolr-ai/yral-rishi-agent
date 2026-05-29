from fastapi import APIRouter, HTTPException

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
async def etl_status():
    """Operator view of the chat-ai → v2 ETL cursor.

    Public (no auth) for now — cutover-readiness only, contains no PII; we
    can add auth gating once mobile-facing endpoints are cutover. Returns
    last_sync_ts + last_error + rows_pulled per table.
    """
    from services.etl_chat_ai import get_status
    from datetime import datetime

    pool = await database.get_pool()
    raw = await get_status(pool)
    # Serialize timestamps for JSON
    for t in raw["tables"]:
        for k in ("last_sync_ts", "last_run_at"):
            v = t.get(k)
            if isinstance(v, datetime):
                t[k] = v.isoformat()
    return raw
