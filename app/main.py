import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import sentry_sdk

import database
from auth import get_current_user
import config
from infra import init_sentry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {config.APP_NAME} v{config.APP_VERSION}")
    logger.info(f"Environment: {config.ENVIRONMENT}")

    try:
        await database.get_pool()
        logger.info("Database pool initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database pool at startup: {e}")

    trending_refresher_task = asyncio.create_task(_trending_stats_refresher())

    yield

    logger.info("Shutting down...")
    trending_refresher_task.cancel()
    try:
        await trending_refresher_task
    except asyncio.CancelledError:
        pass
    await database.close_pool()
    logger.info("Shutdown complete")


async def _trending_stats_refresher():
    """Refresh influencer_trending_stats materialized view every 15 min.

    First refresh is non-concurrent (required when view has no data).
    Subsequent refreshes use CONCURRENTLY to avoid blocking reads.
    Both replicas run this — Postgres row-level locking handles the race.
    """
    REFRESH_INTERVAL_SEC = 15 * 60

    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("REFRESH MATERIALIZED VIEW influencer_trending_stats")
        logger.info("influencer_trending_stats: initial refresh complete")
    except Exception:
        logger.exception("influencer_trending_stats: initial refresh failed")

    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SEC)
        try:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY influencer_trending_stats"
                )
            logger.info("influencer_trending_stats: concurrent refresh complete")
        except Exception:
            logger.exception("influencer_trending_stats: concurrent refresh failed")


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

if config.CORS_ORIGINS == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in config.CORS_ORIGINS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=("*" not in origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def capture_validation_error(request: Request, exc: RequestValidationError):
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("http.status_code", "422")
        scope.set_tag("http.method", request.method)
        scope.set_tag("http.route", request.url.path)
        scope.fingerprint = ["422", request.method, request.url.path]
        scope.set_context("validation", {
            "path": str(request.url.path),
            "method": request.method,
            "errors": exc.errors(),
        })
        sentry_sdk.capture_message(
            f"422 {request.method} {request.url.path}",
            level="warning",
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/api/v1/auth/me", tags=["Auth"])
async def auth_me(request: Request):
    user_id = get_current_user(request)
    return {"user_id": user_id}


from routes.health import router as health_router
app.include_router(health_router)

from routes.influencers import router as influencers_router
app.include_router(influencers_router)

from routes.chat import router as chat_router
app.include_router(chat_router)

from routes.chat_v2 import router as chat_v2_router
app.include_router(chat_v2_router)

from routes.media import router as media_router
app.include_router(media_router)
