"""Port endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import BBox, EngineDep, clean
from api.schemas import Port
from maritime import queries

router = APIRouter(prefix="/ports", tags=["ports"])


@router.get("", response_model=list[Port])
def list_ports(engine: EngineDep, bbox: BBox) -> list[Port]:
    df = queries.ports(engine)
    if bbox is not None:
        a, b, x, y = bbox
        df = df[df.lat.between(a, b) & df.lon.between(x, y)]
    return [Port(**{k: clean(r).get(k) for k in Port.model_fields}) for r in df.to_dict("records")]
