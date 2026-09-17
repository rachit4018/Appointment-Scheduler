import os
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://rachit4018:password@localhost:5432/portal")

# NullPool unconditionally: pytest-asyncio gives each test a fresh event
# loop, and a pooled asyncpg connection stays bound to the loop that opened
# it, so a retained connection blows up the next test. It also sidesteps a
# real bug against Supabase's pooler (PgBouncer, transaction mode): a
# long-lived SQLAlchemy pool holds asyncpg connections open across requests,
# and if one gets killed mid-statement (e.g. a serverless timeout) without
# running its deallocate, the leftover server-side prepared statement
# collides with the next connection's on the same pooled backend
# ("prepared statement ... already exists"). A fresh connection per checkout
# avoids that; disabling asyncpg's own statement cache below limits the
# damage further.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"statement_cache_size": 0},
    pool_size=5,       # Adjust based on serverless scale
    max_overflow=10,
)


SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session



