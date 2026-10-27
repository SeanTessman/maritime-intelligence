"""Cases, evidence provenance, and reports against the test database."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from maritime.config import BoundingBox
from maritime.ingestion.load_database import ingest_file
from maritime.investigations import cases, evidence, reporting
from maritime.sources import SYNTHETIC_AIS

pytestmark = pytest.mark.db
BBOX = BoundingBox(min_lat=40.3, max_lat=41.1, min_lon=-74.4, max_lon=-73.4)


@pytest.fixture()
def loaded(engine, fixture_csv):
    ingest_file(fixture_csv, SYNTHETIC_AIS, engine=engine, bbox=BBOX, block_size=4096)
    with engine.begin() as c:
        ids = (
            c.execute(
                text(
                    "SELECT position_id FROM ais_positions WHERE mmsi='999000001' "
                    "ORDER BY base_datetime LIMIT 3"
                )
            )
            .scalars()
            .all()
        )
        eid = c.execute(
            text(
                "INSERT INTO anomaly_events (run_key, detector, anomaly_type, mmsi, event_time, "
                "explanation, supporting_position_ids) VALUES ('k', 'rule', 'position_jump', '999000001', "
                "now(), 'x', :ids) RETURNING event_id"
            ),
            {"ids": ids},
        ).scalar_one()
    return engine, eid, ids


def test_case_lifecycle_and_neutral_statuses(loaded):
    engine, _, _ = loaded
    cid = cases.create_case("Review", "desc", engine=engine)
    assert cases.get_case(cid, engine)["status"] == "New"
    cases.set_status(cid, "Under review", engine=engine)
    with pytest.raises(cases.CaseError):
        cases.set_status(cid, "Confirmed threat", engine=engine)
    with pytest.raises(cases.CaseError, match="reason"):
        cases.set_status(cid, "Closed", engine=engine)
    cases.set_status(cid, "Closed", "explained by data error", engine=engine)
    hist = cases.status_history(cid, engine)
    assert hist["new_status"].tolist() == ["New", "Under review", "Closed"]
    with pytest.raises(cases.CaseError):
        cases.create_case("   ", engine=engine)


def test_evidence_with_provenance_and_idempotent_add(loaded):
    engine, eid, ids = loaded
    cid = cases.create_case("Jump", engine=engine)
    evidence.add_anomaly(cid, eid, include_positions=True, engine=engine)
    evidence.add_anomaly(cid, eid, include_positions=True, engine=engine)  # no duplicates
    evidence.add_note(cid, "looks like a GNSS error", engine=engine)
    ev = evidence.list_evidence(cid, engine)
    assert ev.evidence_type.value_counts().to_dict() == {"position": 3, "anomaly": 1, "note": 1}
    prov = ev[ev.evidence_type == "anomaly"].provenance.iloc[0]
    assert prov["detector"] == "rule" and prov["data"][0]["source"] == "synthetic_ais_fixture"
    assert prov["data"][0]["is_synthetic"] is True and prov["data"][0]["file_name"].endswith(".csv")
    with pytest.raises(cases.CaseError):
        evidence.add_anomaly(cid, 999999, engine=engine)


def test_case_report_contains_sources_and_limitations(loaded):
    engine, eid, _ = loaded
    cid = cases.create_case("Report", engine=engine)
    evidence.add_anomaly(cid, eid, include_positions=True, engine=engine)
    rep = reporting.case_report(cid, engine)
    md, html = rep.to_markdown(), rep.to_html()
    for text_ in (md, html):
        assert "synthetic_ais_fixture" in text_
        assert "not evidence of illegal or hostile activity" in text_
        assert f"anomaly_events.event_id={eid}" in text_
        assert "Limitations" in text_
    assert rep.to_pdf()[:4] == b"%PDF"
    assert b"metadata" in rep.to_csv()
