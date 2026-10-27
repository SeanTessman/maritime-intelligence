"""Create two sample investigations on SYNTHETIC demo vessels and export their reports.

Real vessels are never used for sample investigations. Run after
`python tasks.py synthetic --load`, `process` and `detect`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from maritime.config import PROJECT_ROOT
from maritime.database import get_engine
from maritime.investigations import cases, evidence, reporting

OUT = PROJECT_ROOT / "reports" / "sample_investigations"

SCENARIOS = [
    (
        "reporting_gap",
        "SYNTHETIC demo: mid-region reporting gap",
        "Synthetic vessel stops reporting mid-harbour for ~3 h and reappears further along its route.",
        "Gap is mid-region (not near the study-area edge or a port). Checked neighbouring "
        "synthetic vessels: no common outage in this synthetic scenario. In real data the next "
        "step would be to check receiver coverage and satellite AIS before drawing conclusions.",
        "Insufficient evidence",
        "Gap alone cannot distinguish coverage loss from a powered-down unit.",
    ),
    (
        "position_jump",
        "SYNTHETIC demo: impossible position jump",
        "One fix is ~15-30 km from its neighbours, implying a speed far above any vessel's.",
        "Single isolated outlier fix; previous and next fixes are consistent with each other. "
        "Pattern matches a GNSS/decoding error rather than movement.",
        "Explained",
        "Single-fix outlier consistent with a position error (synthetic injection).",
    ),
]


def main() -> None:
    eng = get_engine()
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (atype, title, desc, note, status, reason) in enumerate(SCENARIOS, start=1):
        ev = pd.read_sql(
            text(
                """SELECT a.event_id FROM anomaly_events a JOIN vessels v USING (mmsi)
               WHERE v.is_synthetic AND a.anomaly_type = :t ORDER BY a.event_time LIMIT 1"""
            ),
            eng,
            params={"t": atype},
        )
        if ev.empty:
            print(f"no synthetic {atype} event found; skipping")
            continue
        existing = pd.read_sql(
            text("SELECT case_id FROM investigation_cases WHERE title = :t"),
            eng,
            params={"t": title},
        )
        cid = (
            int(existing.case_id[0])
            if len(existing)
            else cases.create_case(title, desc, engine=eng)
        )
        evidence.add_anomaly(cid, int(ev.event_id[0]), include_positions=True, engine=eng)
        if not len(existing):
            evidence.add_note(cid, note, engine=eng)
            cases.set_status(cid, "Under review", "triage started", engine=eng)
            cases.set_status(cid, status, reason, engine=eng)
        rep = reporting.case_report(cid, eng)
        stem = OUT / f"sample_case_{i}_{atype}"
        Path(f"{stem}.html").write_text(rep.to_html())
        Path(f"{stem}.md").write_text(rep.to_markdown())
        Path(f"{stem}.pdf").write_bytes(rep.to_pdf())
        print(f"case {cid}: {title} -> {stem}.html/.md/.pdf")


if __name__ == "__main__":
    main()
