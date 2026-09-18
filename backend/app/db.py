import os
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://rachit4018:password@localhost:5432/portal")

# NullPool: pytest-asyncio gives each test a fresh event loop, and a pooled
# connection stays bound to the loop that opened it, so a retained
# connection blows up the next test. It's also required against Supabase's
# pooler (PgBouncer, transaction mode), which hands out a different backend
# per transaction — a long-lived client-side pool doesn't match that model.
#
# prepare_threshold=None tells psycopg to never create server-side prepared
# statements at all. Unlike asyncpg, which always names and prepares
# statements server-side (and needs workarounds to avoid PgBouncer naming
# collisions), psycopg can just skip preparing entirely, which is the
# actual documented fix for this driver rather than a mitigation.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"prepare_threshold": None},
)


SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session



