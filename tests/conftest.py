"""Shared fixtures.

DB tests (marked `db`) run against a separate database, `miadp_test` by default, or
MIADP_TEST_DATABASE_URL. The schema is rebuilt from the Alembic migrations once per
session and tables are truncated between tests. If the database is unreachable the
DB tests are SKIPPED (reported as skipped, never as passed).
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from maritime.config import PROJECT_ROOT, get_settings

FIXTURES = Path(__file__).parent / "fixtures"
TABLES = (
    "case_status_history",
    "case_evidence",
    "investigation_cases",
    "anomaly_events",
    "model_runs",
    "port_proximity_events",
    "voyages",
    "ais_positions",
    "vessels",
    "ports",
    "ingestion_runs",
    "data_sources",
)


def _test_url() -> str:
    if url := os.environ.get("MIADP_TEST_DATABASE_URL"):
        return url
    url = make_url(get_settings().sqlalchemy_url).set(database="miadp_test")
    return url.render_as_string(hide_password=False)


def _alembic(url: str, *cmd: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    getattr(command, cmd[0])(cfg, *cmd[1:])


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_url()
    try:
        eng = create_engine(url)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unavailable ({type(exc).__name__}); see docs/deployment.md")
    _alembic(url, "downgrade", "base")
    _alembic(url, "upgrade", "head")
    return url


@pytest.fixture()
def engine(db_url: str):
    eng = create_engine(db_url, connect_args={"options": "-c timezone=UTC"})
    with eng.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def fixture_csv() -> Path:
    path = FIXTURES / "synthetic_ais_small.csv"
    if not path.exists():
        from maritime.synthetic import make_fixture_csv

        make_fixture_csv(path)
    return path


def make_positions(rows: list[tuple]) -> pd.DataFrame:
    """Helper: rows of (mmsi, iso_time, lat, lon, sog, cog)."""
    df = pd.DataFrame(rows, columns=["mmsi", "base_datetime", "lat", "lon", "sog", "cog"])
    df["base_datetime"] = pd.to_datetime(df["base_datetime"], utc=True)
    df["position_id"] = range(1, len(df) + 1)
    return df
