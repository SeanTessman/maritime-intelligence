"""Vessel endpoints."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.deps import EngineDep, Limit, MMSIPath, Offset, TimeWindow, clean
from api.schemas import Page, Position, TrackFeatures, Vessel
from maritime import queries
from maritime.config import get_settings
from maritime.ingestion.validate_ais import describe_flags
from maritime.processing.vessel_features import FEATURE_COLUMNS, vessel_type_group

router = APIRouter(prefix="/vessels", tags=["vessels"])


def _vessel(rec: dict) -> Vessel:
    rec = clean(rec)
    return Vessel(
        **{k: rec.get(k) for k in Vessel.model_fields if k != "vessel_type_group"},
        vessel_type_group=vessel_type_group(rec.get("vessel_type")),
    )


@router.get("", response_model=Page[Vessel])
def list_vessels(
    engine: EngineDep,
    q: str | None = Query(None, max_length=64, description="MMSI or name substring"),
    include_synthetic: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[Vessel]:
    df, total = queries.search_vessels(q, include_synthetic, limit, offset, engine)
    return Page(
        items=[_vessel(r) for r in df.to_dict("records")], total=total, limit=limit, offset=offset
    )


@router.get("/{mmsi}", response_model=Vessel, responses={404: {"description": "unknown MMSI"}})
def get_vessel(mmsi: MMSIPath, engine: EngineDep) -> Vessel:
    v = queries.vessel(mmsi, engine)
    if v is None:
        raise HTTPException(404, f"vessel {mmsi} not found")
    return _vessel(v.to_dict())


@router.get("/{mmsi}/positions", response_model=Page[Position])
def get_positions(
    mmsi: MMSIPath,
    engine: EngineDep,
    window: TimeWindow,
    limit: int = Query(1000, ge=1, le=queries.MAX_POSITIONS),
    offset: Offset = 0,
) -> Page[Position]:
    if queries.vessel(mmsi, engine) is None:
        raise HTTPException(404, f"vessel {mmsi} not found")
    df, total = queries.vessel_positions(mmsi, window[0], window[1], limit, offset, engine)
    items = []
    for r in df.to_dict("records"):
        r = clean(r)
        r["quality_flags"] = int(r.get("quality_flags") or 0)
        r["quality_flag_names"] = describe_flags(r["quality_flags"])
        items.append(Position(**{k: r.get(k) for k in Position.model_fields}))
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{mmsi}/features", response_model=list[TrackFeatures])
def get_features(mmsi: MMSIPath, engine: EngineDep) -> list[TrackFeatures]:
    if queries.vessel(mmsi, engine) is None:
        raise HTTPException(404, f"vessel {mmsi} not found")
    proc = get_settings().processed_dir
    path = proc / "track_features.parquet"
    if not path.exists():
        raise HTTPException(503, "features not computed yet; run `python tasks.py process`")
    f = pd.read_parquet(path, filters=[("mmsi", "==", mmsi)])
    scores_path = proc / "track_scores.parquet"
    if scores_path.exists():
        s = pd.read_parquet(
            scores_path, columns=["track_id", "score", "split"], filters=[("mmsi", "==", mmsi)]
        )
        f = f.merge(s, on="track_id", how="left")
    out = []
    for r in f.to_dict("records"):
        feats = {
            c: (None if pd.isna(r[c]) or np.isinf(r[c]) else float(r[c])) for c in FEATURE_COLUMNS
        }
        out.append(
            TrackFeatures(
                track_id=r["track_id"],
                mmsi=mmsi,
                track_date=r["track_date"]
                if isinstance(r["track_date"], date)
                else pd.Timestamp(r["track_date"]).date(),
                vessel_type_group=r["vessel_type_group"],
                features=feats,
                anomaly_score=None if pd.isna(r.get("score", np.nan)) else float(r["score"]),
                score_split=r.get("split") if isinstance(r.get("split"), str) else None,
            )
        )
    return out
