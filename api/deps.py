"""Shared FastAPI dependencies and validators."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd
from fastapi import Depends, HTTPException, Path, Query
from sqlalchemy import Engine

from api.schemas import MMSI_PATTERN
from maritime.database import get_engine

MAX_RANGE = timedelta(days=31)


def engine_dep() -> Engine:
    return get_engine()


EngineDep = Annotated[Engine, Depends(engine_dep)]
MMSIPath = Annotated[str, Path(pattern=MMSI_PATTERN, description="9-digit MMSI")]
Limit = Annotated[int, Query(ge=1, le=500, description="page size (max 500)")]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def time_window(
    start: datetime | None = Query(None, description="ISO-8601, UTC if no offset"),
    end: datetime | None = Query(None),
) -> tuple[datetime | None, datetime | None]:
    start = _utc(start)
    end = _utc(end)
    if start and end:
        if end < start:
            raise HTTPException(422, "end must be after start")
        if end - start > MAX_RANGE:
            raise HTTPException(422, f"time range limited to {MAX_RANGE.days} days")
    return start, end


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    ts = pd.Timestamp(t)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


TimeWindow = Annotated[tuple[datetime | None, datetime | None], Depends(time_window)]


def bbox_params(
    min_lat: float | None = Query(None, ge=-90, le=90),
    max_lat: float | None = Query(None, ge=-90, le=90),
    min_lon: float | None = Query(None, ge=-180, le=180),
    max_lon: float | None = Query(None, ge=-180, le=180),
) -> tuple[float, float, float, float] | None:
    vals = (min_lat, max_lat, min_lon, max_lon)
    if all(v is None for v in vals):
        return None
    if any(v is None for v in vals):
        raise HTTPException(422, "bbox needs all of min_lat, max_lat, min_lon, max_lon")
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(422, "bbox minimums must be less than maximums")
    return vals  # type: ignore[return-value]


BBox = Annotated[tuple[float, float, float, float] | None, Depends(bbox_params)]


def clean(rec: dict) -> dict:
    """Replace NaN/NaT/pd.NA with None for JSON serialisation."""
    out = {}
    for k, v in rec.items():
        try:
            out[k] = (
                None
                if (v is None or (not isinstance(v, list | dict | tuple) and pd.isna(v)))
                else v
            )
        except (TypeError, ValueError):
            out[k] = v
    return out
