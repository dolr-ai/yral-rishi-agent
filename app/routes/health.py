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


# ─── On-demand drain + reconciliation (Pieces B + C, 2026-06-11 plan) ────
#
