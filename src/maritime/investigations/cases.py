"""Investigation cases: create, list, update status (with history).

Statuses are deliberately neutral. There is no label for "guilty", "hostile",
"threat" or similar; the schema's CHECK constraint enforces this too.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from maritime.database import get_engine

CASE_STATUSES = ("New", "Under review", "Explained", "Insufficient evidence", "Closed")


class CaseError(ValueError):
    pass


def create_case(
    title: str,
    description: str | None = None,
    created_by: str = "local-analyst",
    engine: Engine | None = None,
) -> int:
    title = (title or "").strip()
    if not title:
        raise CaseError("A case needs a title.")
    with (engine or get_engine()).begin() as c:
        cid = c.execute(
            text(
                "INSERT INTO investigation_cases (title, description, created_by) "
                "VALUES (:t, :d, :u) RETURNING case_id"
            ),
            {"t": title[:200], "d": description, "u": created_by},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO case_status_history (case_id, old_status, new_status, "
                "changed_by, reason) VALUES (:c, NULL, 'New', :u, 'case opened')"
            ),
            {"c": cid, "u": created_by},
        )
    return int(cid)


def get_case(case_id: int, engine: Engine | None = None) -> dict[str, Any] | None:
    with (engine or get_engine()).connect() as c:
        row = (
            c.execute(text("SELECT * FROM investigation_cases WHERE case_id = :c"), {"c": case_id})
            .mappings()
            .first()
        )
    return dict(row) if row else None


def list_cases(engine: Engine | None = None) -> pd.DataFrame:
    return pd.read_sql(
        text(
            """SELECT c.case_id, c.title, c.status, c.created_by, c.created_at, c.updated_at,
                  COUNT(e.evidence_id) AS evidence_items
           FROM investigation_cases c LEFT JOIN case_evidence e USING (case_id)
           GROUP BY c.case_id ORDER BY c.updated_at DESC"""
        ),
        engine or get_engine(),
    )


def set_status(
    case_id: int,
    new_status: str,
    reason: str | None = None,
    changed_by: str = "local-analyst",
    engine: Engine | None = None,
) -> None:
    if new_status not in CASE_STATUSES:
        raise CaseError(f"Status must be one of {CASE_STATUSES}")
    if (
        new_status in ("Explained", "Insufficient evidence", "Closed")
        and not (reason or "").strip()
    ):
        raise CaseError("Give a short reason when resolving or closing a case.")
    eng = engine or get_engine()
    with eng.begin() as c:
        old = c.execute(
            text("SELECT status FROM investigation_cases WHERE case_id = :c FOR UPDATE"),
            {"c": case_id},
        ).scalar_one_or_none()
        if old is None:
            raise CaseError(f"Case {case_id} not found")
        if old == new_status:
            return
        c.execute(
            text(
                "UPDATE investigation_cases SET status = :s, updated_at = now() WHERE case_id = :c"
            ),
            {"s": new_status, "c": case_id},
        )
        c.execute(
            text(
                "INSERT INTO case_status_history (case_id, old_status, new_status, "
                "changed_by, reason) VALUES (:c, :o, :n, :u, :r)"
            ),
            {"c": case_id, "o": old, "n": new_status, "u": changed_by, "r": reason},
        )


def status_history(case_id: int, engine: Engine | None = None) -> pd.DataFrame:
    return pd.read_sql(
        text(
            "SELECT old_status, new_status, changed_by, reason, changed_at "
            "FROM case_status_history WHERE case_id = :c ORDER BY changed_at"
        ),
        engine or get_engine(),
        params={"c": case_id},
    )
