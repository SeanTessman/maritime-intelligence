"""Attach evidence to cases, with provenance captured at the moment it is added.

Provenance answers: which dataset did this come from, which ingestion run/file loaded
it, which detector and model version produced it, and when it was added.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Engine, text

from maritime.database import get_engine
from maritime.investigations.cases import CaseError


def _touch(c: Connection, case_id: int) -> None:
    c.execute(
        text("UPDATE investigation_cases SET updated_at = now() WHERE case_id = :c"), {"c": case_id}
    )


def position_provenance(c: Connection, position_ids: list[int]) -> list[dict[str, Any]]:
    if not position_ids:
        return []
    rows = (
        c.execute(
            text(
                """SELECT ds.name AS source, ds.publisher, ds.url, ds.is_synthetic,
                  ir.file_name, ir.file_sha256, COUNT(*) AS n_positions
           FROM ais_positions p
           JOIN data_sources ds ON ds.source_id = p.source_id
           LEFT JOIN ingestion_runs ir ON ir.run_id = p.run_id
           WHERE p.position_id = ANY(:ids)
           GROUP BY 1, 2, 3, 4, 5, 6"""
            ),
            {"ids": list(position_ids)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def anomaly_provenance(c: Connection, event_id: int) -> dict[str, Any]:
    ev = (
        c.execute(
            text(
                """SELECT a.event_id, a.detector, a.anomaly_type, a.run_key, a.mmsi, a.event_time,
                  a.supporting_position_ids, m.model_name, m.model_version, m.artifact_path
           FROM anomaly_events a LEFT JOIN model_runs m USING (model_run_id)
           WHERE a.event_id = :e"""
            ),
            {"e": event_id},
        )
        .mappings()
        .first()
    )
    if ev is None:
        raise CaseError(f"Anomaly event {event_id} not found")
    return {
        "detector": ev["detector"],
        "anomaly_type": ev["anomaly_type"],
        "run_key": ev["run_key"],
        "model": ev["model_name"],
        "model_version": ev["model_version"],
        "model_artifact": ev["artifact_path"],
        "data": position_provenance(c, list(ev["supporting_position_ids"] or [])),
        "n_supporting_positions": len(ev["supporting_position_ids"] or []),
    }


def add_anomaly(
    case_id: int,
    event_id: int,
    include_positions: bool = False,
    added_by: str = "local-analyst",
    engine: Engine | None = None,
) -> int:
    """Attach an anomaly (and optionally its supporting positions) to a case."""
    with (engine or get_engine()).begin() as c:
        prov = anomaly_provenance(c, event_id)
        eid = c.execute(
            text(
                """INSERT INTO case_evidence (case_id, evidence_type, anomaly_event_id, provenance, added_by)
               VALUES (:c, 'anomaly', :e, CAST(:p AS jsonb), :u)
               ON CONFLICT (case_id, anomaly_event_id) WHERE anomaly_event_id IS NOT NULL
               DO UPDATE SET provenance = EXCLUDED.provenance
               RETURNING evidence_id"""
            ),
            {"c": case_id, "e": event_id, "p": json.dumps(prov, default=str), "u": added_by},
        ).scalar_one()
        if include_positions:
            ids = (
                c.execute(
                    text("SELECT supporting_position_ids FROM anomaly_events WHERE event_id=:e"),
                    {"e": event_id},
                ).scalar_one()
                or []
            )
            _add_positions(c, case_id, list(ids), added_by)
        _touch(c, case_id)
    return int(eid)


def _add_positions(c: Connection, case_id: int, position_ids: list[int], added_by: str) -> int:
    n = 0
    for pid in position_ids:
        prov = position_provenance(c, [pid])
        res = c.execute(
            text(
                """INSERT INTO case_evidence (case_id, evidence_type, position_id, provenance, added_by)
               VALUES (:c, 'position', :p, CAST(:pr AS jsonb), :u)
               ON CONFLICT (case_id, position_id) WHERE position_id IS NOT NULL DO NOTHING"""
            ),
            {
                "c": case_id,
                "p": int(pid),
                "pr": json.dumps(prov[0] if prov else {}, default=str),
                "u": added_by,
            },
        )
        n += res.rowcount or 0
    return n


def add_positions(
    case_id: int,
    position_ids: list[int],
    added_by: str = "local-analyst",
    engine: Engine | None = None,
) -> int:
    with (engine or get_engine()).begin() as c:
        n = _add_positions(c, case_id, position_ids, added_by)
        _touch(c, case_id)
    return n


def add_note(
    case_id: int, note: str, added_by: str = "local-analyst", engine: Engine | None = None
) -> int:
    note = (note or "").strip()
    if not note:
        raise CaseError("Note is empty.")
    with (engine or get_engine()).begin() as c:
        eid = c.execute(
            text(
                "INSERT INTO case_evidence (case_id, evidence_type, note, added_by) "
                "VALUES (:c, 'note', :n, :u) RETURNING evidence_id"
            ),
            {"c": case_id, "n": note[:5000], "u": added_by},
        ).scalar_one()
        _touch(c, case_id)
    return int(eid)


def list_evidence(case_id: int, engine: Engine | None = None) -> pd.DataFrame:
    return pd.read_sql(
        text(
            """SELECT e.evidence_id, e.evidence_type, e.anomaly_event_id, e.position_id,
                  e.proximity_id, e.note, e.provenance, e.added_by, e.added_at,
                  a.anomaly_type, a.detector, a.mmsi AS anomaly_mmsi, a.event_time, a.score,
                  a.explanation, p.mmsi AS position_mmsi, p.base_datetime, p.lat, p.lon, p.sog
           FROM case_evidence e
           LEFT JOIN anomaly_events a ON a.event_id = e.anomaly_event_id
           LEFT JOIN ais_positions p ON p.position_id = e.position_id
           WHERE e.case_id = :c ORDER BY e.added_at, e.evidence_id"""
        ),
        engine or get_engine(),
        params={"c": case_id},
    )
