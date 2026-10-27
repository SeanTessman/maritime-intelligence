"""Anomaly endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query

from api.deps import BBox, EngineDep, Limit, Offset, TimeWindow, clean
from api.schemas import Anomaly, AnomalyDetail, Page, Position
from maritime import queries
from maritime.ingestion.validate_ais import describe_flags

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("", response_model=Page[Anomaly])
def list_anomalies(
    engine: EngineDep,
    window: TimeWindow,
    bbox: BBox,
    anomaly_type: list[str] | None = Query(None, max_length=10),
    detector: Literal["rule", "isolation_forest"] | None = None,
    mmsi: str | None = Query(None, pattern=r"^[0-9]{9}$"),
    include_synthetic: bool = False,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[Anomaly]:
    df, total = queries.anomalies(
        anomaly_type,
        detector,
        mmsi,
        window[0],
        window[1],
        include_synthetic,
        limit=limit if bbox is None else 500,
        offset=offset if bbox is None else 0,
        engine=engine,
    )
    if bbox is not None:  # post-filter: events have point locations
        a, b, x, y = bbox
        df = df[df.lat.between(a, b) & df.lon.between(x, y)]
        total = len(df)
        df = df.iloc[offset : offset + limit]
    items = [
        Anomaly(**{k: clean(r).get(k) for k in Anomaly.model_fields}) for r in df.to_dict("records")
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{event_id}", response_model=AnomalyDetail)
def get_anomaly(engine: EngineDep, event_id: int = Path(ge=1)) -> AnomalyDetail:
    rec = queries.anomaly_detail(event_id, engine)
    if rec is None:
        raise HTTPException(404, f"anomaly {event_id} not found")
    pos = []
    for p in rec.pop("supporting_positions").to_dict("records"):
        p = clean(p)
        p["quality_flags"] = int(p.get("quality_flags") or 0)
        p["quality_flag_names"] = describe_flags(p["quality_flags"])
        pos.append(Position(**{k: p.get(k) for k in Position.model_fields}))
    rec = clean(rec)
    rec["supporting_position_ids"] = [int(i) for i in rec.get("supporting_position_ids") or []]
    return AnomalyDetail(
        **{k: rec.get(k) for k in AnomalyDetail.model_fields if k != "supporting_positions"},
        supporting_positions=pos,
    )
