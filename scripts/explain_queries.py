"""Run EXPLAIN (ANALYZE, BUFFERS) on representative queries and save the plans.

Output: sql/explain/<name>.txt plus sql/explain/SUMMARY.md (execution time and whether the
expected index appears in the plan). Measured on whatever database .env points to.
"""

from __future__ import annotations

import platform
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from maritime.database import get_engine

OUT = Path(__file__).resolve().parents[1] / "sql" / "explain"

QUERIES = {
    "trajectory_one_vessel": (
        """SELECT position_id, base_datetime, lat, lon, sog,
                  ST_Distance(geog, LAG(geog) OVER w) AS step_m
           FROM ais_positions WHERE mmsi = '477737800'
             AND base_datetime BETWEEN '2023-01-09' AND '2023-01-16'
           WINDOW w AS (PARTITION BY mmsi ORDER BY base_datetime) ORDER BY base_datetime""",
        "ix_ais_positions_mmsi_time",
    ),
    "spatial_bbox_window": (
        """SELECT COUNT(*) FROM ais_positions
           WHERE ST_Intersects(geog, ST_MakeEnvelope(-74.10, 40.60, -74.00, 40.70, 4326)::geography)
             AND base_datetime BETWEEN '2023-01-11 12:00+00' AND '2023-01-11 15:00+00'""",
        "ix_ais_positions_geog",
    ),
    "port_knn": (
        """SELECT port_id FROM ports ORDER BY geog <-> ST_SetSRID(ST_MakePoint(-74.05, 40.65), 4326)::geography
           LIMIT 5""",
        "ix_ports_geog",
    ),
    "proximity_dwithin_one_day": (
        """SELECT p.position_id, np.port_id FROM ais_positions p
           CROSS JOIN LATERAL (SELECT pt.port_id FROM ports pt WHERE ST_DWithin(p.geog, pt.geog, 2500)
                               ORDER BY p.geog <-> pt.geog LIMIT 1) np
           WHERE p.base_datetime BETWEEN '2023-01-09' AND '2023-01-10'""",
        "ix_ports_geog",
    ),
    "density_grid_full_scan": (
        """SELECT floor(lat / 0.01), floor(lon / 0.01), COUNT(*) FROM ais_positions GROUP BY 1, 2""",
        None,
    ),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    eng = get_engine()
    rows = []
    with eng.connect() as c:
        c.execute(text("ANALYZE ais_positions"))
        c.execute(text("ANALYZE ports"))
        n = c.execute(text("SELECT count(*) FROM ais_positions")).scalar_one()
        ver = c.execute(text("SELECT version()")).scalar_one().split(",")[0]
        for name, (sql, idx) in QUERIES.items():
            plan = "\n".join(r[0] for r in c.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql)))
            (OUT / f"{name}.txt").write_text(plan + "\n")
            ms = float(re.search(r"Execution Time: ([\d.]+) ms", plan).group(1))
            used = "n/a (full scan expected)" if idx is None else ("yes" if idx in plan else "NO")
            rows.append(f"| {name} | {ms:.1f} | {idx or '-'} | {used} |")
    summary = [
        "# EXPLAIN ANALYZE summary",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {ver}, {platform.machine()} "
        f"({platform.platform()}), ais_positions = {n:,} rows. Single run, warm cache.",
        "",
        "| query | execution ms | expected index | index used |",
        "|---|---|---|---|",
        *rows,
        "",
        "Full plans are in the .txt files next to this summary.",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
