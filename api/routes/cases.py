"""Investigation cases (read-only over the API; edits happen in the local dashboard)."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import EngineDep, clean
from api.schemas import Case
from maritime.investigations.cases import list_cases

router = APIRouter(prefix="/cases", tags=["investigations"])


@router.get("", response_model=list[Case])
def cases(engine: EngineDep) -> list[Case]:
    return [
        Case(**{k: clean(r).get(k) for k in Case.model_fields})
        for r in list_cases(engine).to_dict("records")
    ]
