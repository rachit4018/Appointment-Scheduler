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
    """psycopg (v3) works for both sync and async engines under the same
    +psycopg URL, unlike asyncpg/psycopg2 which needed separate drivers."""
    return os.getenv(
        "DATABASE_URL", "postgresql+psycopg://portal:portal@localhost:5432/portal"
    )
 
 
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