"""Database integration: schema, constraints, idempotent + restart-safe ingestion, spatial SQL."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from maritime.config import BoundingBox
from maritime.ingestion.load_database import ingest_file
from maritime.sources import SYNTHETIC_AIS

pytestmark = pytest.mark.db

# The fixture's synthetic tracks sit in NY Harbor; this box contains all of them.
BBOX = BoundingBox(min_lat=40.3, max_lat=41.1, min_lon=-74.4, max_lon=-73.4)


def _count(engine, sql: str) -> int:
    with engine.connect() as c:
        return int(c.execute(text(sql)).scalar_one())


def test_schema_has_all_tables_and_postgis(engine):
    with engine.connect() as c:
        names = set(
            c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars()
        )
        assert c.execute(text("SELECT postgis_lib_version()")).scalar_one()
    for t in (
        "vessels",
        "ais_positions",
        "ports",
        "voyages",
        "port_proximity_events",
        "anomaly_events",
        "model_runs",
        "investigation_cases",
        "case_evidence",
        "ingestion_runs",
        "data_sources",
    ):
        assert t in names


def test_spatial_and_composite_indexes_exist(engine):
    with engine.connect() as c:
        idx = set(c.execute(text("SELECT indexname FROM pg_indexes")).scalars())
    assert {"ix_ais_positions_geog", "ix_ais_positions_mmsi_time", "ix_ports_geog"} <= idx


def test_constraints_reject_bad_rows(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO data_sources (name, publisher) VALUES ('t','t')"))
    bad_statements = [
        "INSERT INTO vessels (mmsi, source_id) VALUES ('12AB', 1)",  # MMSI format
        "INSERT INTO ais_positions (mmsi, base_datetime, lat, lon, source_id) "
        "VALUES ('999999999', now(), 1, 1, 1)",  # FK vessel
        "INSERT INTO investigation_cases (title, status) VALUES ('x', 'Confirmed threat')",  # status
    ]
    for stmt in bad_statements:
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(stmt))
    with engine.begin() as c:
        c.execute(text("INSERT INTO vessels (mmsi, source_id) VALUES ('366000001', 1)"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ais_positions (mmsi, base_datetime, lat, lon, source_id) "
                "VALUES ('366000001', now(), 95, 1, 1)"
            )
        )  # lat range


def test_ingestion_is_idempotent(engine, fixture_csv):
    first = ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    n = _count(engine, "SELECT count(*) FROM ais_positions")
    assert first["status"] == "completed" and n == first["inserted"] > 0
    # In-bbox hard rejects: bad MMSI, missing MMSI, bad timestamp. Rows with missing or
    # impossible coordinates can't be placed in the bbox and are counted file-wide instead.
    assert first["rejected"] == 3
    assert first["invalid_coordinates_filewide"] == 2
    second = ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    assert second["status"] == "skipped_already_completed"
    # Even with the run record removed, ON CONFLICT prevents duplicate positions.
    with engine.begin() as c:
        c.execute(text("UPDATE ais_positions SET run_id = NULL"))
        c.execute(text("DELETE FROM ingestion_runs"))
    third = ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    assert third["inserted"] == 0
    assert _count(engine, "SELECT count(*) FROM ais_positions") == n


def test_ingestion_resumes_after_interruption(engine, fixture_csv):
    with pytest.raises(RuntimeError, match="Simulated failure"):
        ingest_file(
            fixture_csv,
            SYNTHETIC_AIS,
            engine=engine,
            bbox=BBOX,
            block_size=4096,
            fail_after_chunk=1,
        )
    with engine.connect() as c:
        run = c.execute(text("SELECT status, last_chunk_done FROM ingestion_runs")).one()
    assert run.status == "failed" and run.last_chunk_done == 1
    partial = _count(engine, "SELECT count(*) FROM ais_positions")
    resumed = ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    assert resumed["status"] == "completed"
    total = _count(engine, "SELECT count(*) FROM ais_positions")
    assert total > partial
    # A clean single-pass load gives the same final row count.
    with engine.begin() as c:
        c.execute(text("TRUNCATE ais_positions, vessels, ingestion_runs RESTART IDENTITY CASCADE"))
    clean = ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    assert clean["inserted"] == total


def test_mmsi_preserved_and_source_marked_synthetic(engine, fixture_csv):
    ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    with engine.connect() as c:
        mmsis = c.execute(text("SELECT mmsi FROM vessels ORDER BY mmsi")).scalars().all()
        syn = c.execute(text("SELECT bool_and(is_synthetic) FROM vessels")).scalar_one()
    assert all(len(m) == 9 and m.startswith("9990") for m in mmsis)
    assert "999000000" in mmsis and syn is True


def test_spatial_dwithin_and_distance(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO data_sources (name, publisher) VALUES ('t','t')"))
        c.execute(
            text(
                "INSERT INTO ports (wpi_number, port_name, lat, lon, source_id) VALUES "
                "(1, 'A', 40.70, -74.00, 1), (2, 'B', 40.80, -74.00, 1)"
            )
        )
        rows = c.execute(
            text(
                "SELECT port_name, ST_Distance(geog, ST_SetSRID(ST_MakePoint(-74.0, 40.71), 4326)"
                "::geography) AS d FROM ports WHERE ST_DWithin(geog, "
                "ST_SetSRID(ST_MakePoint(-74.0, 40.71), 4326)::geography, 2000) ORDER BY d"
            )
        ).all()
    assert [r.port_name for r in rows] == ["A"]
    assert rows[0].d == pytest.approx(1111, rel=0.01)  # 0.01 deg latitude ~ 1.11 km


def test_window_function_gap_query(engine, fixture_csv):
    ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    df = pd.read_sql(
        text(
            "SELECT mmsi, EXTRACT(EPOCH FROM base_datetime - LAG(base_datetime) OVER "
            "(PARTITION BY mmsi ORDER BY base_datetime)) AS dt FROM ais_positions"
        ),
        engine,
    )
    assert (df["dt"].dropna() >= 0).all()
