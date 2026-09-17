import os
import sys
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://rachit4018:portal@localhost:5432/portal")

# Under pytest, pooling is turned off. pytest-asyncio runs each test on a
# fresh event loop, and a pooled asyncpg connection stays bound to the loop
# that opened it, so a retained connection blows up the next test. NullPool
# opens and closes per use, which costs nothing at test volumes.


_TESTING = "pytest" in sys.modules or os.getenv("TESTING") == "1"


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    # Supabase's connection pooler runs PgBouncer in transaction mode, which
    # is incompatible with asyncpg's server-side prepared statement cache.
    # Disabling it is a no-op against a direct (non-pooled) connection, so
    # this is safe regardless of which Supabase connection string is used.
    connect_args={"statement_cache_size": 0},
    **({"poolclass": NullPool} if _TESTING else {}),
)


SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session



