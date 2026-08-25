import os
from logging.config import fileConfig
 
from alembic import context
from sqlalchemy import create_engine, pool
 
from app.models import Base
 
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
 
target_metadata = Base.metadata
 
 
def _sync_url() -> str:
    """Alembic runs synchronously; strip the asyncpg driver from the URL."""
    url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://portal:portal@localhost:5432/portal"
    )
    return url.replace("+asyncpg", "")
 
 
def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
 
 
def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
 
 
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()