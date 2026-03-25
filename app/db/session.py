import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# asyncpg requires SSL to be passed via connect_args, not the URL query string
_ssl_ctx: ssl.SSLContext | bool = False
if settings.DB_SSLMODE == "require":
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

_connect_args: dict = {"ssl": _ssl_ctx} if _ssl_ctx else {}
# command_timeout: max seconds asyncpg waits for a single DB operation.
# 30 s covers complex discover-feed queries that JOIN/sort/geo-filter many rows.
_connect_args["command_timeout"] = 30

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,          # validate connections before use
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,           # recycle idle connections every 30 min
    pool_timeout=30,             # max seconds to wait for a free connection
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


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
