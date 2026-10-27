"""Health check."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import EngineDep
from api.schemas import Health
from maritime import DISCLAIMER, __version__
from maritime.database import check_connection

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def health(engine: EngineDep) -> Health:
    ok, msg = check_connection(engine)
    return Health(
        status="ok" if ok else "degraded",
        database=msg,
        database_ok=ok,
        version=__version__,
        disclaimer=DISCLAIMER,
    )
