"""Alembic environment. The database URL comes from maritime.config (env vars), never alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from maritime.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url() -> str:
    return config.attributes.get("url") or get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _url(), poolclass=pool.NullPool, connect_args={"options": "-c timezone=UTC"}
    )
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
