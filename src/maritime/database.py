"""Database engine and session helpers (SQLAlchemy 2.x + psycopg 3)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from maritime.config import get_settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """Return a pooled engine. Cached so the app reuses one pool per URL."""
    # Force the session time zone to UTC so timestamptz values come back in UTC regardless
    # of the server's or client's local settings.
    return create_engine(
        url or get_settings().sqlalchemy_url,
        pool_pre_ping=True,
        pool_size=5,
        connect_args={"options": "-c timezone=UTC"},
    )


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional session: commit on success, roll back on any exception."""
    maker = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def read_sql(
    sql: str, params: dict[str, Any] | None = None, engine: Engine | None = None
) -> pd.DataFrame:
    """Run a parameterized SELECT and return a DataFrame. Never interpolate user input into `sql`."""
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def check_connection(engine: Engine | None = None) -> tuple[bool, str]:
    """Return (ok, message) describing database + PostGIS availability."""
    try:
        with (engine or get_engine()).connect() as conn:
            version = conn.execute(text("SELECT postgis_lib_version()")).scalar_one()
        return True, f"Connected (PostGIS {version})"
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a readable message
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
