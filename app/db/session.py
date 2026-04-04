import logging
import ssl

from asyncpg.exceptions import ConnectionDoesNotExistError, TooManyConnectionsError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

_log = logging.getLogger(__name__)

# asyncpg requires SSL to be passed via connect_args, not the URL query string
_ssl_ctx: ssl.SSLContext | bool = False
if settings.DB_SSLMODE == "require":
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

_connect_args: dict = {"ssl": _ssl_ctx} if _ssl_ctx else {}
# command_timeout: max seconds asyncpg waits for a single DB operation.
_connect_args["command_timeout"] = 30
# TCP keepalives — prevent the cloud DB (RDS/Supabase) from silently closing
# idle connections, which causes the SSL MAC / ConnectionDoesNotExistError.
_connect_args["server_settings"] = {
    "tcp_keepalives_idle":     "60",   # start sending keepalives after 60s idle
    "tcp_keepalives_interval": "10",   # retry every 10s
    "tcp_keepalives_count":    "5",    # drop connection after 5 failed probes
}

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,          # validate connections before use (SELECT 1)
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,            # recycle idle connections every 5 min
                                 # (cloud DBs often drop idle after 5–10 min)
    pool_timeout=30,             # max seconds to wait for a free connection
    pool_reset_on_return="rollback",  # clean state when returning to pool
    echo=settings.DEBUG,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# Errors that indicate a stale/dropped connection (safe to retry once)
_RETRYABLE = (ConnectionDoesNotExistError,)


async def get_db() -> AsyncSession:  # type: ignore[override]
    """
    Yield a DB session. If the first attempt hits a dropped-connection error
    (the most common cause of transient 500s), the engine pool is purged and
    the request is retried once with a fresh connection.
    """
    for attempt in range(2):
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
                return
            except DBAPIError as exc:
                await session.rollback()
                orig = getattr(exc, "orig", None)
                if attempt == 0 and isinstance(orig, _RETRYABLE):
                    # Stale connection — dispose the pool so all idle
                    # connections are dropped and rebuilt on next use.
                    _log.warning(
                        "Stale DB connection detected (%s). Purging pool and retrying.",
                        type(orig).__name__,
                    )
                    await engine.dispose(close=False)
                    continue  # retry
                raise
            except Exception:
                await session.rollback()
                raise
