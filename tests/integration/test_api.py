"""API tests against the test database (FastAPI TestClient)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.deps import engine_dep
from api.main import app
from maritime.config import BoundingBox
from maritime.ingestion.load_database import ingest_file
from maritime.sources import SYNTHETIC_AIS

pytestmark = pytest.mark.db
BBOX = BoundingBox(min_lat=40.3, max_lat=41.1, min_lon=-74.4, max_lon=-73.4)


@pytest.fixture()
def client(engine, fixture_csv):
    ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    with engine.begin() as c:
        pid = c.execute(
            text("SELECT min(position_id) FROM ais_positions WHERE mmsi='999000000'")
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO anomaly_events (run_key, detector, anomaly_type, mmsi, event_time, lat, lon, "
                "explanation, supporting_position_ids, rule_flags) VALUES ('rules_test', 'rule', "
                "'reporting_gap', '999000000', '2030-01-07T02:00:00Z', 40.6, -74.0, 'test explanation', "
                ":ids, CAST(:rf AS jsonb))"
            ),
            {"ids": [pid], "rf": json.dumps({"rule": "reporting_gap"})},
        )
        c.execute(text("INSERT INTO data_sources (name, publisher) VALUES ('ports_t', 't')"))
        c.execute(
            text(
                "INSERT INTO ports (wpi_number, port_name, lat, lon, source_id) VALUES "
                "(1, 'Test Port', 40.7, -74.0, (SELECT max(source_id) FROM data_sources))"
            )
        )
    app.dependency_overrides[engine_dep] = lambda: engine
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database_ok"] is True
    assert "not evidence of illegal" in r.json()["disclaimer"]


def test_vessel_ids_valid_invalid_unknown(client):
    assert client.get("/vessels/999000000").json()["mmsi"] == "999000000"
    bad = client.get("/vessels/12AB")
    assert bad.status_code == 422 and bad.json()["error"] == "validation_error"
    missing = client.get("/vessels/366999999")
    assert missing.status_code == 404 and missing.json()["error"] == "http_error"


def test_synthetic_hidden_by_default(client):
    assert client.get("/vessels").json()["total"] == 0
    assert client.get("/vessels?include_synthetic=true").json()["total"] == 5


def test_pagination_bounds(client):
    r = client.get("/vessels/999000000/positions?limit=10&offset=5").json()
    assert r["limit"] == 10 and r["offset"] == 5 and len(r["items"]) == 10 and r["total"] >= 15
    times = [i["base_datetime"] for i in r["items"]]
    assert times == sorted(times)
    assert client.get("/vessels?limit=0").status_code == 422
    assert client.get("/vessels?limit=501").status_code == 422
    assert client.get("/vessels?offset=-1").status_code == 422


def test_empty_results(client):
    r = client.get(
        "/vessels/999000000/positions?start=2031-01-01T00:00:00Z&end=2031-01-02T00:00:00Z"
    )
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["total"] == 0
    assert (
        client.get("/anomalies?anomaly_type=position_jump&include_synthetic=true").json()["total"]
        == 0
    )


def test_date_and_bbox_validation(client):
    assert client.get("/anomalies?start=2030-01-10&end=2030-01-01").status_code == 422
    assert client.get("/anomalies?start=2030-01-01&end=2030-03-01").status_code == 422
    assert client.get("/ports?min_lat=41&max_lat=40&min_lon=-74&max_lon=-73").status_code == 422
    assert client.get("/ports?min_lat=40").status_code == 422
    assert client.get("/ports?min_lat=95&max_lat=96&min_lon=-74&max_lon=-73").status_code == 422
    ports = client.get("/ports?min_lat=40.6&max_lat=40.8&min_lon=-74.1&max_lon=-73.9").json()
    assert [p["port_name"] for p in ports] == ["Test Port"]


def test_anomaly_list_and_detail_with_evidence(client):
    lst = client.get("/anomalies?include_synthetic=true").json()
    assert lst["total"] == 1
    eid = lst["items"][0]["event_id"]
    d = client.get(f"/anomalies/{eid}").json()
    assert d["anomaly_type"] == "reporting_gap" and len(d["supporting_positions"]) == 1
    assert client.get("/anomalies/999999").status_code == 404
    assert client.get("/anomalies/0").status_code == 422


def test_ingestion_runs_and_cases(client):
    runs = client.get("/ingestion/runs").json()
    assert runs[0]["status"] == "completed" and runs[0]["rows_inserted"] > 0
    assert client.get("/cases").json() == []


def test_openapi_documents_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/health",
        "/vessels",
        "/vessels/{mmsi}",
        "/vessels/{mmsi}/positions",
        "/vessels/{mmsi}/features",
        "/anomalies",
        "/anomalies/{event_id}",
        "/ports",
        "/networks/vessels/{mmsi}",
        "/ingestion/runs",
    ):
        assert p in paths
