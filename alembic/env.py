import os
import sys
from logging.config import fileConfig
from sqlalchemy import pool, create_engine
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engine.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Objects that exist in the live DB but are created by scripts, not models.py.
# They are managed by hand (see 0001_baseline.py) and must not be dropped by
# autogenerate.
_DB_ONLY_INDEXES = {
    "ix_doc_fts", "ix_doc_body_trgm", "ix_doc_lower_body_trgm",
    "ix_doc_summary_trgm", "ix_doc_case_number_trgm", "ix_doc_cited_provisions",
}
_DB_ONLY_COLUMNS = {("documents", "fts")}


def _include_object(obj, name, type_, reflected, compare_to):
    if type_ == "index" and name in _DB_ONLY_INDEXES:
        return False
    if type_ == "column" and (obj.table.name, name) in _DB_ONLY_COLUMNS:
        return False
    return True


def get_url() -> str:
    url = os.environ.get('DATABASE_URL', config.get_main_option('sqlalchemy.url'))
    # Alembic needs sync driver — replace asyncpg with psycopg2 or pg8000
    return url.replace('+asyncpg', '+psycopg2').replace('+aiosqlite', '')


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=_include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
