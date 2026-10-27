"""Processing job: positions in the DB -> trajectories, features, proximity events.

Outputs
  * voyages                (DB)  trajectory segments with simplified LineString tracks
  * port_proximity_events  (DB)  computed in SQL with ST_DWithin + gaps-and-islands
  * data/processed/steps.parquet           per-fix step metrics + gap context
  * data/processed/track_features.parquet  per vessel-day features
All writes are upserts, so re-running is safe and keeps ids stable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from shapely.geometry import LineString
from sqlalchemy import Engine, text

from maritime.config import get_settings
from maritime.database import get_engine
from maritime.processing.spatial_features import density_lookup, nearest_port
from maritime.processing.trajectories import (
    TrajectoryParams,
    add_step_metrics,
    build_segments,
    classify_gaps,
    identity_collision_suspects,
    prepare_positions,
    segment_trajectories,
)
from maritime.processing.vessel_features import compute_track_features

logger = logging.getLogger(__name__)

PROXIMITY_SQL = """
WITH near AS (
    SELECT p.position_id, p.mmsi, p.base_datetime, np.port_id, np.d
    FROM ais_positions p
    CROSS JOIN LATERAL (
        SELECT pt.port_id, ST_Distance(p.geog, pt.geog) AS d
        FROM ports pt
        WHERE ST_DWithin(p.geog, pt.geog, :radius)
        ORDER BY p.geog <-> pt.geog
        LIMIT 1
    ) np
),
marked AS (
    SELECT *,
           CASE WHEN LAG(base_datetime) OVER w IS NULL
                  OR base_datetime - LAG(base_datetime) OVER w > make_interval(secs => :merge_gap)
                THEN 1 ELSE 0 END AS brk
    FROM near
    WINDOW w AS (PARTITION BY mmsi, port_id ORDER BY base_datetime)
),
islands AS (
    SELECT *, SUM(brk) OVER (PARTITION BY mmsi, port_id ORDER BY base_datetime) AS event_no
    FROM marked
)
INSERT INTO port_proximity_events
    (mmsi, port_id, start_time, end_time, n_obs, min_distance_m, radius_m, position_ids)
SELECT mmsi, port_id, MIN(base_datetime), MAX(base_datetime), COUNT(*), MIN(d), :radius,
       ARRAY_AGG(position_id ORDER BY base_datetime)
FROM islands
GROUP BY mmsi, port_id, event_no
ON CONFLICT (mmsi, port_id, start_time, radius_m) DO UPDATE SET
    end_time = EXCLUDED.end_time, n_obs = EXCLUDED.n_obs,
    min_distance_m = EXCLUDED.min_distance_m, position_ids = EXCLUDED.position_ids
"""

POSITIONS_SQL = """
SELECT p.position_id, p.mmsi, p.base_datetime, p.lat, p.lon, p.sog, p.cog, p.heading,
       p.nav_status, p.quality_flags, v.vessel_type, v.is_synthetic
FROM ais_positions p JOIN vessels v USING (mmsi)
ORDER BY p.mmsi, p.base_datetime
"""


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    path = path or get_settings().miadp_thresholds_file
    return yaml.safe_load(path.read_text())


def load_ports_df(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(text("SELECT port_id, port_name, lat, lon FROM ports"), engine)


def compute_steps(
    positions: pd.DataFrame, ports: pd.DataFrame, cfg: dict[str, Any]
) -> tuple[pd.DataFrame, dict]:
    """Pure-pandas trajectory pipeline shared by the DB job and the synthetic benchmark."""
    params = TrajectoryParams.from_config(cfg["trajectory"])
    clean, removed = prepare_positions(positions)
    steps = segment_trajectories(add_step_metrics(clean), params)
    pid, pdist = nearest_port(steps["lat"].to_numpy(), steps["lon"].to_numpy(), ports)
    steps["nearest_port_id"] = pid
    steps["port_dist_m"] = pdist
    # Density is computed separately for real and synthetic fixes so synthetic demo data
    # can never change real-data features.
    if "is_synthetic" in steps:
        steps["density_log10"] = steps.groupby("is_synthetic", group_keys=False).apply(
            density_lookup, include_groups=False
        )
    else:
        steps["density_log10"] = density_lookup(steps)
    steps["gap_context"] = classify_gaps(steps, get_settings().bbox, steps["port_dist_m"], params)
    collisions = identity_collision_suspects(steps)
    stats = {
        **removed,
        "positions": len(steps),
        "segments": int(steps.groupby("mmsi")["segment_id"].nunique().sum()),
        "gaps": int(steps["is_gap_start"].sum()),
        "jumps": int(steps["is_jump"].sum()),
        "gap_contexts": steps["gap_context"].value_counts().to_dict(),
        "possible_identity_collisions": int(collisions.sum()),
    }
    return steps, stats


def _segment_rows(steps: pd.DataFrame, ports: pd.DataFrame, radius_m: float) -> pd.DataFrame:
    seg = build_segments(steps)
    if seg.empty:
        return seg
    for end in ("start", "end"):
        pid, d = nearest_port(seg[f"{end}_lat"].to_numpy(), seg[f"{end}_lon"].to_numpy(), ports)
        seg[f"{end}_port_id"] = np.where(d <= radius_m, pid, None)
    tracks = []
    for (_, _), g in steps.groupby(["mmsi", "segment_id"], sort=False):
        if len(g) < 2:
            tracks.append(None)
            continue
        line = LineString(g[["lon", "lat"]].to_numpy()).simplify(0.00005, preserve_topology=False)
        tracks.append(line.wkt if line.length > 0 else None)
    seg["track_wkt"] = tracks
    return seg


def write_voyages(
    engine: Engine, seg: pd.DataFrame, method: str = "gap_jump_segmentation_v1"
) -> int:
    rows = seg.assign(method=method).to_dict("records")
    sql = text(
        """
        INSERT INTO voyages (mmsi, segment_index, start_time, end_time, n_obs, distance_km,
            start_port_id, end_port_id, gap_before_s, gap_before_context, method, track)
        VALUES (:mmsi, :segment_id, :start_time, :end_time, :n_obs, :distance_km,
            :start_port_id, :end_port_id, :gap_before_s, :gap_before_context, :method,
            ST_GeomFromText(:track_wkt, 4326))
        ON CONFLICT (mmsi, start_time, method) DO UPDATE SET
            segment_index = EXCLUDED.segment_index, end_time = EXCLUDED.end_time,
            n_obs = EXCLUDED.n_obs, distance_km = EXCLUDED.distance_km,
            start_port_id = EXCLUDED.start_port_id, end_port_id = EXCLUDED.end_port_id,
            gap_before_s = EXCLUDED.gap_before_s, gap_before_context = EXCLUDED.gap_before_context,
            track = EXCLUDED.track
        """
    )
    clean = []
    for r in rows:
        r = {
            k: (None if (v is pd.NA or (isinstance(v, float) and np.isnan(v))) else v)
            for k, v in r.items()
        }
        for k in ("start_port_id", "end_port_id"):
            r[k] = int(r[k]) if r[k] is not None else None
        r["segment_id"] = int(r["segment_id"])
        r["n_obs"] = int(r["n_obs"])
        r.setdefault("gap_before_context", None)
        clean.append(r)
    with engine.begin() as conn:
        for i in range(0, len(clean), 2000):
            conn.execute(sql, clean[i : i + 2000])
    return len(clean)


def write_proximity_events(engine: Engine, radius_m: float, merge_gap_s: float) -> int:
    with engine.begin() as conn:
        conn.execute(text(PROXIMITY_SQL), {"radius": radius_m, "merge_gap": merge_gap_s})
        return int(
            conn.execute(
                text("SELECT count(*) FROM port_proximity_events WHERE radius_m = :r"),
                {"r": radius_m},
            ).scalar_one()
        )


def run_processing(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or get_engine()
    cfg = load_thresholds()
    settings = get_settings()
    ports = load_ports_df(engine)
    if ports.empty:
        raise RuntimeError("No ports loaded. Run `python tasks.py ports` first.")
    logger.info("Loading positions from database ...")
    positions = pd.read_sql(text(POSITIONS_SQL), engine)
    positions["base_datetime"] = pd.to_datetime(positions["base_datetime"], utc=True)
    steps, stats = compute_steps(positions, ports, cfg)
    logger.info("Steps computed: %s", stats)

    radius = float(cfg["proximity"]["radius_m"])
    seg = _segment_rows(steps, ports, radius)
    n_voyages = write_voyages(engine, seg)
    n_prox = write_proximity_events(engine, radius, float(cfg["proximity"]["merge_gap_s"]))

    feats = compute_track_features(steps, cfg["trajectory"]["gap_threshold_s"], radius)
    syn = positions.groupby("mmsi")["is_synthetic"].first()
    feats["is_synthetic"] = feats["mmsi"].map(syn).fillna(False).astype(bool)
    out = settings.processed_dir
    out.mkdir(parents=True, exist_ok=True)
    steps.to_parquet(out / "steps.parquet", index=False)
    feats.to_parquet(out / "track_features.parquet", index=False)
    return {
        **stats,
        "voyages_upserted": n_voyages,
        "proximity_events": n_prox,
        "tracks": len(feats),
        "outputs": [str(out / "steps.parquet"), str(out / "track_features.parquet")],
    }
