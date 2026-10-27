"""Benchmark representative operations and write reports/benchmarks/results.{json,md}.

Each measurement records what was run, on how much data, and how many repetitions.
Anything not run is reported as "not measured" (never estimated).

The ingestion benchmark loads into a throwaway database `miadp_bench`; the role needs CREATEDB
and the PostGIS extension must exist there (Docker image: automatic; native install:
`CREATE EXTENSION postgis` as a superuser).

    python scripts/benchmark.py [--ingest-file data/raw/AIS_2023_01_09.zip] [--skip-ingest]
                                [--dashboard-url http://127.0.0.1:8501]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from maritime import queries
from maritime.config import PROJECT_ROOT, get_settings
from maritime.database import get_engine

OUT = PROJECT_ROOT / "reports" / "benchmarks"


def hardware() -> dict:
    mem = None
    with contextlib.suppress(ValueError, OSError, AttributeError):
        mem = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_gb": mem,
        "python": platform.python_version(),
    }


def bench_ingest(path: Path) -> dict:
    """Load one real file into a throwaway database (miadp_bench) so the main DB is untouched."""
    from alembic import command
    from alembic.config import Config

    from maritime.ingestion.load_database import ingest_file
    from maritime.sources import NOAA_AIS

    main = make_url(get_settings().sqlalchemy_url)
    admin = create_engine(main.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname='miadp_bench'")).first():
            c.execute(text("CREATE DATABASE miadp_bench"))
    url = main.set(database="miadp_bench").render_as_string(hide_password=False)
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(url, connect_args={"options": "-c timezone=UTC"})
    t0 = time.perf_counter()
    r = ingest_file(path, NOAA_AIS, engine=eng)
    secs = time.perf_counter() - t0
    eng.dispose()
    return {
        "file": path.name,
        "file_mb": round(path.stat().st_size / 1e6, 1),
        "seconds": round(secs, 1),
        "raw_rows": r["total_raw_observations"],
        "inserted_rows": r["inserted"],
        "raw_rows_per_s": round(r["total_raw_observations"] / secs),
        "method": "single run: stream zip -> validate -> bbox filter -> COPY + upsert, fresh schema",
    }


def timed(fn, reps: int) -> dict:  # noqa: ANN001
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return {
        "reps": reps,
        "median_ms": round(statistics.median(ts), 1),
        "min_ms": round(min(ts), 1),
        "max_ms": round(max(ts), 1),
    }


def bench_queries() -> dict:
    eng = get_engine()
    vessels = pd.read_sql(
        text("SELECT mmsi FROM vessels WHERE NOT is_synthetic ORDER BY mmsi LIMIT 20"), eng
    ).mmsi
    traj = []
    sizes = []
    for m in vessels:
        t0 = time.perf_counter()
        df, _ = queries.vessel_positions(m, limit=5000, engine=eng)
        traj.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(df))
    s = datetime(2023, 1, 11, 12, tzinfo=UTC)
    e = datetime(2023, 1, 11, 15, tzinfo=UTC)
    bbox = (40.60, 40.70, -74.10, -74.00)
    spatial = timed(lambda: queries.positions_in_bbox(bbox, s, e, engine=eng), 10)
    knn = timed(lambda: queries.nearest_ports(40.65, -74.05, 5, engine=eng), 20)
    return {
        "trajectory_retrieval": {
            "vessels": len(traj),
            "median_ms": round(statistics.median(traj), 1),
            "max_ms": round(max(traj), 1),
            "median_fixes_returned": int(statistics.median(sizes)),
            "method": "vessel_positions() incl. LAG/ST_Distance, up to 5,000 fixes",
        },
        "spatial_bbox_3h": {**spatial, "method": "ST_Intersects envelope + 3 h window"},
        "port_knn": {**knn, "method": "geography <-> KNN, k=5"},
    }


def bench_proximity_and_features() -> dict:
    from maritime.processing.pipeline import (
        POSITIONS_SQL,
        compute_steps,
        load_ports_df,
        load_thresholds,
        write_proximity_events,
    )
    from maritime.processing.vessel_features import compute_track_features

    eng = get_engine()
    cfg = load_thresholds()
    t0 = time.perf_counter()
    n = write_proximity_events(
        eng, float(cfg["proximity"]["radius_m"]), float(cfg["proximity"]["merge_gap_s"])
    )
    prox_s = time.perf_counter() - t0
    pos = pd.read_sql(text(POSITIONS_SQL), eng)
    pos["base_datetime"] = pd.to_datetime(pos["base_datetime"], utc=True)
    ports = load_ports_df(eng)
    t0 = time.perf_counter()
    steps, _ = compute_steps(pos, ports, cfg)
    t_steps = time.perf_counter() - t0
    t0 = time.perf_counter()
    feats = compute_track_features(steps)
    t_feats = time.perf_counter() - t0
    return {
        "proximity_sql_full_table": {
            "seconds": round(prox_s, 1),
            "positions": len(pos),
            "events": n,
            "method": "PROXIMITY_SQL (ST_DWithin + LATERAL KNN + islands), upsert, single run",
        },
        "trajectory_steps": {
            "seconds": round(t_steps, 1),
            "positions": len(pos),
            "method": "compute_steps(), single run",
        },
        "track_features": {
            "seconds": round(t_feats, 1),
            "tracks": len(feats),
            "method": "compute_track_features(), single run",
        },
    }


def bench_dashboard(url: str) -> dict:
    from playwright.sync_api import sync_playwright

    exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe)
        page = b.new_page()
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            page.goto(url, wait_until="load")
            page.wait_for_selector("[data-testid='stMetric']", timeout=120_000)
            page.wait_for_selector(".js-plotly-plot", timeout=120_000)
            times.append(round((time.perf_counter() - t0) * 1000))
            if i == 0:
                page.wait_for_timeout(500)
        b.close()
    return {
        "page": "Executive Overview",
        "runs_ms": times,
        "method": "headless Chromium, navigation until first metric + first Plotly chart rendered; "
        "run 1 is a cold Streamlit cache, runs 2-3 are cached",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-file", default=str(get_settings().raw_dir / "AIS_2023_01_09.zip"))
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--dashboard-url", default="http://127.0.0.1:8501")
    args = ap.parse_args()
    res: dict = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "hardware": hardware(),
    }
    with get_engine().connect() as c:
        res["dataset"] = {
            "ais_positions_rows": c.execute(
                text("SELECT count(*) FROM ais_positions")
            ).scalar_one(),
            "postgres": c.execute(text("SELECT version()")).scalar_one().split(",")[0],
        }
    p = Path(args.ingest_file)
    res["ingestion"] = (
        "not measured (--skip-ingest)"
        if args.skip_ingest
        else (bench_ingest(p) if p.exists() else f"not measured ({p} missing)")
    )
    res["queries"] = bench_queries()
    res["processing"] = bench_proximity_and_features()
    try:
        res["dashboard_load"] = bench_dashboard(args.dashboard_url)
    except Exception as exc:  # noqa: BLE001
        res["dashboard_load"] = f"not measured ({type(exc).__name__}: dashboard not reachable)"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=2, default=str))
    md = [
        "# Benchmark results",
        "",
        f"Measured {res['measured_at']} on `{res['hardware']['platform']}`, "
        f"{res['hardware']['cpu_count']} vCPU, {res['hardware']['ram_gb']} GB RAM, {res['dataset']['postgres']}, "
        f"ais_positions = {res['dataset']['ais_positions_rows']:,} rows.",
        "",
        "These are single-machine measurements from `scripts/benchmark.py`; rerun on your own hardware.",
        "",
        "```json",
        json.dumps(
            {k: v for k, v in res.items() if k not in ("hardware", "measured_at")},
            indent=2,
            default=str,
        ),
        "```",
    ]
    (OUT / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
