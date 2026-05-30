import asyncio
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import database
from auth import get_current_user
from infra import init_sentry
from middleware import RequestIdMiddleware
from rate_limiter import RateLimitMiddleware, hydrate_from_db
from services import langfuse_tracing, nudge, proactive, websocket_manager
from routes.chat import router as chat_router
from routes.chat_v2 import router as chat_v2_router
from routes.chat_v3 import router as chat_v3_router
from routes.creator import router as creator_router
from routes.creator_coach import router as creator_coach_router
from routes.creator_takeover import router as creator_takeover_router
from routes.wizard import router as wizard_router
from routes.earnings import router as earnings_router
from routes.admin_dashboard import router as admin_dashboard_router
from routes.health import router as health_router
from routes.human_chat import router as human_chat_router
from routes.influencers import router as influencers_router
from routes.media import router as media_router
from routes.memories import router as memories_router
from routes.websocket import router as ws_router

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
    redis_sub_task = asyncio.create_task(websocket_manager.start_redis_subscriber())
    engagement_task = asyncio.create_task(_engagement_loop())
    takeover_sweep_task = asyncio.create_task(_takeover_timeout_sweep())
    from services.memory_consolidation import consolidation_loop

    memory_consolidation_task = asyncio.create_task(consolidation_loop())

    from services.quality_scorer import scoring_loop

    quality_scoring_task = asyncio.create_task(scoring_loop())

    from services.streak_tracker import streak_loop

    streak_task = asyncio.create_task(streak_loop())

    from services.etl_chat_ai import etl_loop

    etl_task = asyncio.create_task(etl_loop())

    from services.etl_integrity import integrity_loop

    integrity_task = asyncio.create_task(integrity_loop())

    from services.email_digest import digest_loop

    digest_task = asyncio.create_task(digest_loop())

    # Hydrate rate-limit config from DB into Redis so the middleware
    # reads the operator-tuned values, not just the defaults. Idempotent.
    try:
        await hydrate_from_db(await database.get_pool())
    except Exception as e:
        logger.warning("rate_limiter hydrate failed (limiter uses defaults): %s", e)

    yield

    logger.info("Shutting down...")
    trending_refresher_task.cancel()
    redis_sub_task.cancel()
    engagement_task.cancel()
    takeover_sweep_task.cancel()
    memory_consolidation_task.cancel()
    quality_scoring_task.cancel()
    streak_task.cancel()
    etl_task.cancel()
    integrity_task.cancel()
    digest_task.cancel()
    try:
        await trending_refresher_task
    except asyncio.CancelledError:
        pass
    try:
        await redis_sub_task
    except asyncio.CancelledError:
        pass
    try:
        await engagement_task
    except asyncio.CancelledError:
        pass
    try:
        await takeover_sweep_task
    except asyncio.CancelledError:
        pass
    try:
        await memory_consolidation_task
    except asyncio.CancelledError:
        pass
    try:
        await quality_scoring_task
    except asyncio.CancelledError:
        pass
    try:
        await streak_task
    except asyncio.CancelledError:
        pass
    try:
        await etl_task
    except asyncio.CancelledError:
        pass
    try:
        await integrity_task
    except asyncio.CancelledError:
        pass
    try:
        await digest_task
    except asyncio.CancelledError:
        pass
    langfuse_tracing.flush()
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


async def _takeover_timeout_sweep():
    """Auto-release Chat-as-Human takeovers after 2 min of creator inactivity.

    Runs every 5 seconds (Bug 2 fix: was 30s — closes a 36s window where the
    creator could keep typing past the spec'd timeout).

    Bug 3 fix: uses deactivate_if_active() for atomic flip + idempotency. Only
    the caller that actually flipped the row to FALSE posts the 'left' message,
    preventing duplicates when manual release races with the sweep.

    Uses idx_conversations_active_takeover partial index — scan is cheap.
    """
    SWEEP_INTERVAL_SEC = 5
    TIMEOUT_MINUTES = 2

    await asyncio.sleep(10)  # Brief warmup before first sweep

    from repositories import takeover_repo, message_repo

    while True:
        try:
            pool = await database.get_pool()
            timed_out = await takeover_repo.find_timed_out_takeovers(
                pool, timeout_minutes=TIMEOUT_MINUTES
            )
            for row in timed_out:
                try:
                    # Atomic flip + idempotency: only proceed if WE flipped it.
                    was_active = await takeover_repo.deactivate_if_active(
                        pool, row["id"]
                    )
                    if not was_active:
                        continue  # Manual release beat us — skip duplicate message
                    creator_display = row.get("bot_name") or "Creator"
                    await message_repo.create(
                        pool,
                        conversation_id=row["id"],
                        role="system",
                        content=f"{creator_display} has left the chat.",
                        message_type="text",
                        sender_id=row.get("human_creator_user_id"),
                    )
                    await websocket_manager.broadcast_event(
                        row["user_id"],
                        "human_creator_takeover_ended",
                        {"conversation_id": row["id"], "reason": "timeout"},
                    )
                except Exception:
                    logger.debug(
                        f"Takeover auto-release failed for conv {row.get('id')}"
                    )
            if timed_out:
                logger.info(f"Auto-released {len(timed_out)} takeovers (timeout)")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Takeover timeout sweep error (will retry)")

        await asyncio.sleep(SWEEP_INTERVAL_SEC)


async def _engagement_loop():
    """Run proactive messages + nudges every 15 min.

    Proactive: find conversations idle for 24h, send bot-initiated check-ins.
    Nudge: find conversations idle for 5-10 min with few messages, send follow-ups.
    Only one replica should run this — Postgres row-level locking prevents duplicates.
    """
    INTERVAL_SEC = 15 * 60

    # Wait 2 min after startup before first run (let the app stabilize)
    await asyncio.sleep(120)

    while True:
        try:
            pool = await database.get_pool()

            # Proactive messages for 24h-idle conversations
            inactive = await proactive.find_inactive_conversations(
                pool, hours=24, limit=20
            )
            for conv in inactive:
                try:
                    await proactive.send_proactive_message(
                        pool,
                        influencer_id=conv["influencer_id"],
                        user_id=conv["user_id"],
                        conversation_id=conv["id"],
                        trigger_type="welcome_back",
                    )
                except Exception:
                    logger.debug(f"Proactive send failed for conv {conv['id']}")

            # Nudges for recently idle conversations

            recent_convs = await pool.fetch(
                """
                SELECT c.id, c.influencer_id FROM conversations c
                WHERE c.conversation_type = 'ai_chat'
                  AND c.influencer_id IS NOT NULL
                  AND c.updated_at > NOW() - INTERVAL '30 minutes'
                  AND c.updated_at < NOW() - INTERVAL '5 minutes'
                LIMIT 50
                """,
            )
            for conv in recent_convs:
                try:
                    if await nudge.should_nudge(pool, conv["id"]):
                        nudge_text = await nudge.generate_nudge(
                            pool, conv["id"], conv["influencer_id"]
                        )
                        if nudge_text:
                            from repositories import message_repo

                            await message_repo.create(
                                pool,
                                conversation_id=conv["id"],
                                role="assistant",
                                content=nudge_text,
                                message_type="text",
                                sender_id=conv["influencer_id"],
                                is_nudge=True,
                            )
                except Exception:
                    logger.debug(f"Nudge failed for conv {conv['id']}")

            logger.info(
                f"Engagement loop: {len(inactive)} proactive, {len(recent_convs)} nudge candidates"
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Engagement loop error (will retry)")

        await asyncio.sleep(INTERVAL_SEC)


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

if config.CORS_ORIGINS == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in config.CORS_ORIGINS.split(",")]

app.add_middleware(RequestIdMiddleware)
app.add_middleware(RateLimitMiddleware)
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
        scope.set_context(
            "validation",
            {
                "path": str(request.url.path),
                "method": request.method,
                "errors": exc.errors(),
            },
        )
        sentry_sdk.capture_message(
            f"422 {request.method} {request.url.path}",
            level="warning",
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/api/v1/auth/me", tags=["Auth"])
async def auth_me(request: Request):
    user_id = get_current_user(request)
    return {"user_id": user_id}


@app.get("/api/v1/debug/whoami", tags=["Debug"])
async def debug_whoami(request: Request):
    """Temporary: decode JWT and return full payload. Remove before cutover."""
    import jwt as pyjwt

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith(("Bearer ", "bearer ")):
        return {"error": "No Bearer token"}
    token = auth_header[7:]
    try:
        payload = pyjwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
            },
            algorithms=["RS256", "HS256"],
        )
        return {"payload": payload, "token_length": len(token)}
    except Exception as e:
        return {"error": str(e)}


app.include_router(health_router)
app.include_router(admin_dashboard_router)
app.include_router(influencers_router)
app.include_router(chat_router)
app.include_router(chat_v2_router)
app.include_router(media_router)
app.include_router(human_chat_router)
app.include_router(chat_v3_router)
app.include_router(creator_router)
app.include_router(creator_coach_router)
app.include_router(creator_takeover_router)
app.include_router(wizard_router)
app.include_router(earnings_router)
app.include_router(memories_router)
app.include_router(ws_router)
