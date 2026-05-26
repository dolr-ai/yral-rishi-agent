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
