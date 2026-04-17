import asyncio
import logging
import ssl
from collections.abc import AsyncGenerator

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
_connect_args["command_timeout"] = 60
# TCP keepalives — prevent the cloud DB from silently closing idle connections.
_connect_args["server_settings"] = {
    "tcp_keepalives_idle":     "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count":    "5",
}

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
    pool_timeout=30,
    pool_reset_on_return="rollback",
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

# Errors that indicate a stale/dropped connection.
_RETRYABLE = (ConnectionDoesNotExistError, TooManyConnectionsError, TimeoutError, asyncio.TimeoutError)


async def get_db() -> AsyncGenerator[AsyncSession, None]:  # type: ignore[override]
    """
    Yield a single DB session per request.

    On a retryable connection error (stale pool entry, timeout), the pool is
    purged so the NEXT request gets a fresh connection, then the exception
    propagates to the db_connection_error_handler middleware which returns 503.

    NOTE: yield must never appear inside a retry loop when used as a FastAPI
    dependency — Python 3.13 raises RuntimeError("generator didn't stop after
    athrow()") if the generator yields a second time after an exception is
    thrown back into it via athrow().
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except DBAPIError as exc:
            await session.rollback()
            orig = getattr(exc, "orig", None)
            if isinstance(orig, _RETRYABLE):
                _log.warning(
                    "Stale DB connection (%s). Purging pool for next request.",
                    type(orig).__name__,
                )
                await engine.dispose(close=False)
            raise
        except _RETRYABLE as exc:
            await session.rollback()
            _log.warning(
                "DB timeout/connection error (%s). Purging pool for next request.",
                type(exc).__name__,
            )
            await engine.dispose(close=False)
            raise
        except Exception:
            await session.rollback()
            raise
