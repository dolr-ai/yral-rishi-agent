import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _read_database_url() -> str:
    # Docker secrets are mounted as files — more secure than env vars
    secret_file = "/run/secrets/database_url"
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    return os.environ["DATABASE_URL"]


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    url = _read_database_url()
    logger.info("Creating database connection pool...")

    _pool = await asyncpg.create_pool(
        dsn=url,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )

    logger.info("Database connection pool created successfully")
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


async def check_db_health() -> bool:
    try:
        pool = await get_pool()
        try:
            await pool.fetchval("SELECT 1 FROM ai_influencers LIMIT 1")
        except asyncpg.UndefinedTableError:
            await pool.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
