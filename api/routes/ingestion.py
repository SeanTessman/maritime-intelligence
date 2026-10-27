"""Ingestion run history."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import EngineDep, clean
from api.schemas import IngestionRun
from maritime import queries

router = APIRouter(prefix="/ingestion", tags=["system"])


@router.get("/runs", response_model=list[IngestionRun])
def runs(engine: EngineDep) -> list[IngestionRun]:
    df = queries.ingestion_runs(engine)
    return [
        IngestionRun(**{k: clean(r).get(k) for k in IngestionRun.model_fields})
        for r in df.to_dict("records")
    ]
