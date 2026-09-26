import asyncio
import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# Serialises pool CREATION, not pool use. Without it, every coroutine that
# calls get_pool() while _pool is None builds its own pool — and only the last
# assignment survives. The rest keep their connections open forever with no
# reference left to close them. See get_pool().
_pool_lock = asyncio.Lock()


def _read_database_url() -> str:
    # Docker secrets are mounted as files — more secure than env vars
    secret_file = "/run/secrets/database_url"
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    return os.environ["DATABASE_URL"]


def _host_list(url: str) -> list[tuple[str, int]]:
    """Pull the (host, port) pairs out of a multi-host libpq URL without
    touching the userinfo — rsplit on '@' so a password containing '@'
    can't confuse us. `postgresql://u:p@h1:5432,h2:5432/db?...` -> both hosts."""
    authority = url.split("://", 1)[1].split("/", 1)[0]
    hostlist = authority.rsplit("@", 1)[-1]
    hosts: list[tuple[str, int]] = []
    for hostport in hostlist.split(","):
        host, _, port = hostport.partition(":")
        hosts.append((host, int(port) if port else 5432))
    return hosts


async def _connect_with_failover(url: str, hosts, **kwargs) -> asyncpg.Connection:
    """Establish one pool connection to the read-write leader.

    The common path is unchanged — asyncpg's own multi-host connect. But
    asyncpg raises CannotConnectNowError on a host that is still "starting
    up" instead of failing over to the next host, which took V2 down on
    2026-07-23 when a wedged replica sat first in DATABASE_URL. Only on
    that error do we iterate the hosts ourselves, connecting with the full
    DSN (auth/ssl still come from it) but overriding host so the DSN's
    target_session_attrs=read-write rejects replicas. Skip anything
    starting up or read-only; return the first reachable leader.
    """
    try:
        return await asyncpg.connect(dsn=url, **kwargs)
    except asyncpg.CannotConnectNowError:
        pass  # a host is starting up and asyncpg won't fail over — do it ourselves

    errors: list[str] = []
    for host, port in hosts:
        try:
            return await asyncpg.connect(dsn=url, host=host, port=port, **kwargs)
        except asyncpg.CannotConnectNowError:
            errors.append(f"{host}: starting up")
        except asyncpg.TargetServerAttributeNotMatched:
            errors.append(f"{host}: read-only replica")
        except (OSError, asyncpg.PostgresConnectionError) as e:
            errors.append(f"{host}: {type(e).__name__}")
    raise asyncpg.CannotConnectNowError(
        "no read-write postgres host reachable: " + "; ".join(errors)
    )


async def get_pool() -> asyncpg.Pool:
    """The one pool, created once however many callers ask at the same moment.

    The lock is the whole point. `_pool` is None on first use and again after
    any failure, and several background loops call this concurrently — the
    takeover sweep, the feed ranker, the classifier. Unguarded, they all found
    `_pool is None`, all ran `create_pool`, and all but the last leaked a live
    pool that nothing could ever close. On 2026-09-25 that filled both Postgres
    replicas with 98 idle connections and took the service down: new pools
    could not be created at all, so /health's database check failed while
    /health/live kept saying the container was fine.
    """
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        # Re-check inside the lock: while we waited, another caller may have
        # finished. Returning here is what stops the second pool being built.
        if _pool is not None:
            return _pool

        url = _read_database_url()
        hosts = _host_list(url)
        logger.info("Creating database connection pool (%d hosts)...", len(hosts))

        async def _connect(*_args, **kwargs):
            return await _connect_with_failover(url, hosts, **kwargs)

        pool = await asyncpg.create_pool(
            min_size=2,
            max_size=10,
            command_timeout=60,
            connect=_connect,
        )
        # Publish only once fully built. If create_pool raises, `_pool` stays
        # None and the next caller retries — with nothing half-created left
        # holding connections.
        _pool = pool

    logger.info("Database connection pool created successfully")
    return _pool


async def close_pool():
    global _pool
    async with _pool_lock:
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
