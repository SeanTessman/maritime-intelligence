"""Parameterized analytical queries shared by the dashboard, the API and reports.

All user-supplied values go through bind parameters. Optional filters are expressed as
`(:param IS NULL OR column = :param)` so the SQL text is constant (no string building
from user input). Real-data views exclude synthetic vessels unless asked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import Engine, bindparam, text

from maritime.database import get_engine

MAX_PAGE = 500
MAX_POSITIONS = 5000


def _df(
    sql: str,
    params: dict[str, Any] | None = None,
    engine: Engine | None = None,
    expanding: tuple[str, ...] = (),
) -> pd.DataFrame:
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    with (engine or get_engine()).connect() as c:
        return pd.read_sql(stmt, c, params=params or {})


# ---------------------------------------------------------------- overview
def overview_counts(
    engine: Engine | None = None, include_synthetic: bool = False
) -> dict[str, Any]:
    row = _df(
        """SELECT COUNT(DISTINCT p.mmsi) AS vessels, COUNT(*) AS observations,
                  MIN(p.base_datetime) AS t_min, MAX(p.base_datetime) AS t_max,
                  MIN(p.lat) AS lat_min, MAX(p.lat) AS lat_max,
                  MIN(p.lon) AS lon_min, MAX(p.lon) AS lon_max
           FROM ais_positions p JOIN vessels v USING (mmsi)
           WHERE (:syn OR NOT v.is_synthetic)""",
        {"syn": include_synthetic},
        engine,
    ).iloc[0]
    an = _df(
        """SELECT a.detector, COUNT(*) AS n FROM anomaly_events a JOIN vessels v USING (mmsi)
                WHERE (:syn OR NOT v.is_synthetic) AND a.run_key IN (SELECT run_key FROM latest_run_keys)
                GROUP BY 1""".replace("latest_run_keys", _LATEST_KEYS),
        {"syn": include_synthetic},
        engine,
    )
    return {**row.to_dict(), "anomalies": dict(zip(an.detector, an.n, strict=True))}


# Latest run key per detector: rules keyed by config hash (most recent created_at),
# model events keyed by the most recent model run.
_LATEST_KEYS = """(
    SELECT DISTINCT ON (detector, right(run_key, 10) = '_synthetic') run_key
    FROM (SELECT detector, run_key, MAX(created_at) AS t FROM anomaly_events GROUP BY 1, 2) k
    ORDER BY detector, right(run_key, 10) = '_synthetic', t DESC
) AS latest_run_keys_inner"""


def activity_by_hour(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT date_trunc('hour', p.base_datetime) AS hour, COUNT(*) AS observations,
                         COUNT(DISTINCT p.mmsi) AS vessels
                  FROM ais_positions p JOIN vessels v USING (mmsi) WHERE NOT v.is_synthetic
                  GROUP BY 1 ORDER BY 1""",
        engine=engine,
    )


def density_grid(
    cell_deg: float = 0.01,
    engine: Engine | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> pd.DataFrame:
    """Observation density on a lat/lon grid (cell centre + count), real vessels only."""
    min_lat, max_lat, min_lon, max_lon = bbox or (-90, 90, -180, 180)
    return _df(
        """SELECT (floor(p.lat / :c) + 0.5) * :c AS lat, (floor(p.lon / :c) + 0.5) * :c AS lon,
                         COUNT(*) AS observations, COUNT(DISTINCT p.mmsi) AS vessels
                  FROM ais_positions p JOIN vessels v USING (mmsi)
                  WHERE NOT v.is_synthetic AND p.lat BETWEEN :a AND :b AND p.lon BETWEEN :x AND :y
                  GROUP BY 1, 2""",
        {"c": cell_deg, "a": min_lat, "b": max_lat, "x": min_lon, "y": max_lon},
        engine,
    )


def sog_distribution(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT width_bucket(p.sog, 0, 40, 40) AS bucket, COUNT(*) AS n
                  FROM ais_positions p JOIN vessels v USING (mmsi)
                  WHERE NOT v.is_synthetic AND p.sog IS NOT NULL GROUP BY 1 ORDER BY 1""",
        engine=engine,
    )


def ingestion_runs(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT run_id, file_name, status, started_at, finished_at, rows_raw, rows_in_bbox,
                         rows_accepted, rows_rejected, rows_duplicate, rows_inserted, error_message
                  FROM ingestion_runs ORDER BY started_at DESC""",
        engine=engine,
    )


# ---------------------------------------------------------------- vessels
def search_vessels(
    q: str | None = None,
    include_synthetic: bool = False,
    limit: int = 50,
    offset: int = 0,
    engine: Engine | None = None,
) -> tuple[pd.DataFrame, int]:
    limit = max(1, min(limit, MAX_PAGE))
    params = {
        "q": f"%{q.strip()}%" if q else None,
        "syn": include_synthetic,
        "lim": limit,
        "off": max(offset, 0),
    }
    where = """(CAST(:q AS text) IS NULL OR v.mmsi LIKE :q OR v.vessel_name ILIKE :q)
               AND (:syn OR NOT v.is_synthetic)"""
    total = int(_df(f"SELECT COUNT(*) AS n FROM vessels v WHERE {where}", params, engine).n[0])
    df = _df(
        f"""SELECT v.mmsi, v.vessel_name, v.imo, v.call_sign, v.vessel_type, v.length_m,
                        v.width_m, v.first_seen, v.last_seen, v.is_synthetic,
                        (SELECT COUNT(*) FROM ais_positions p WHERE p.mmsi = v.mmsi) AS n_positions
                 FROM vessels v WHERE {where}
                 ORDER BY n_positions DESC, v.mmsi LIMIT :lim OFFSET :off""",
        params,
        engine,
    )
    return df, total


def vessel(mmsi: str, engine: Engine | None = None) -> pd.Series | None:
    df = _df(
        """SELECT v.*, (SELECT COUNT(*) FROM ais_positions p WHERE p.mmsi = v.mmsi) AS n_positions
                FROM vessels v WHERE v.mmsi = :m""",
        {"m": mmsi},
        engine,
    )
    return None if df.empty else df.iloc[0]


def vessel_positions(
    mmsi: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = MAX_POSITIONS,
    offset: int = 0,
    engine: Engine | None = None,
) -> tuple[pd.DataFrame, int]:
    """Trajectory extraction with LAG-based step metrics, computed in SQL."""
    limit = max(1, min(limit, MAX_POSITIONS))
    params = {"m": mmsi, "s": start, "e": end, "lim": limit, "off": max(offset, 0)}
    where = """p.mmsi = :m AND (CAST(:s AS timestamptz) IS NULL OR p.base_datetime >= :s)
               AND (CAST(:e AS timestamptz) IS NULL OR p.base_datetime <= :e)"""
    total = int(
        _df(f"SELECT COUNT(*) AS n FROM ais_positions p WHERE {where}", params, engine).n[0]
    )
    df = _df(
        f"""
        SELECT position_id, mmsi, base_datetime, lat, lon, sog, cog, heading, nav_status, quality_flags,
               EXTRACT(EPOCH FROM base_datetime - LAG(base_datetime) OVER w) AS dt_s,
               ST_Distance(geog, LAG(geog) OVER w) AS step_m
        FROM ais_positions p WHERE {where}
        WINDOW w AS (PARTITION BY mmsi ORDER BY base_datetime)
        ORDER BY base_datetime LIMIT :lim OFFSET :off""",
        params,
        engine,
    )
    return df, total


def vessel_segments(mmsi: str, engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT v.voyage_id, v.segment_index, v.start_time, v.end_time, v.n_obs, v.distance_km,
                         v.gap_before_s, v.gap_before_context, ps.port_name AS start_port,
                         pe.port_name AS end_port
                  FROM voyages v LEFT JOIN ports ps ON ps.port_id = v.start_port_id
                  LEFT JOIN ports pe ON pe.port_id = v.end_port_id
                  WHERE v.mmsi = :m ORDER BY v.start_time""",
        {"m": mmsi},
        engine,
    )


def observation_gaps(
    min_gap_s: float = 1800, limit: int = 200, engine: Engine | None = None
) -> pd.DataFrame:
    """Largest reporting gaps across real vessels (LAG window function)."""
    return _df(
        """
        WITH g AS (
            SELECT p.mmsi, p.position_id, p.base_datetime,
                   LAG(p.base_datetime) OVER w AS prev_time,
                   ST_Distance(p.geog, LAG(p.geog) OVER w) AS dist_m
            FROM ais_positions p JOIN vessels v USING (mmsi) WHERE NOT v.is_synthetic
            WINDOW w AS (PARTITION BY p.mmsi ORDER BY p.base_datetime))
        SELECT mmsi, prev_time AS gap_start, base_datetime AS gap_end,
               EXTRACT(EPOCH FROM base_datetime - prev_time) AS gap_s, dist_m / 1000 AS distance_km
        FROM g WHERE base_datetime - prev_time > make_interval(secs => :mg)
        ORDER BY gap_s DESC LIMIT :lim""",
        {"mg": min_gap_s, "lim": min(limit, MAX_PAGE)},
        engine,
    )


# ---------------------------------------------------------------- ports & proximity
def ports(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT port_id, wpi_number, port_name, alternate_name, unlocode, country, harbor_size,
                         harbor_type, lat, lon FROM ports ORDER BY port_name""",
        engine=engine,
    )


def nearest_ports(lat: float, lon: float, k: int = 5, engine: Engine | None = None) -> pd.DataFrame:
    """KNN with the geography <-> operator (uses the GIST index)."""
    return _df(
        """SELECT port_id, port_name, ST_Distance(geog, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) AS distance_m
                  FROM ports ORDER BY geog <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                  LIMIT :k""",
        {"lat": lat, "lon": lon, "k": k},
        engine,
    )


def port_activity(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT p.port_id, p.port_name, p.lat, p.lon, COUNT(e.proximity_id) AS events,
                         COUNT(DISTINCT e.mmsi) AS vessels,
                         ROUND(SUM(EXTRACT(EPOCH FROM e.end_time - e.start_time)) / 3600.0, 1) AS hours_near
                  FROM ports p LEFT JOIN port_proximity_events e USING (port_id)
                  LEFT JOIN vessels v ON v.mmsi = e.mmsi
                  WHERE v.is_synthetic IS NOT TRUE
                  GROUP BY 1, 2, 3, 4 ORDER BY events DESC""",
        engine=engine,
    )


def proximity_events(
    port_id: int | None = None,
    mmsi: str | None = None,
    limit: int = 200,
    engine: Engine | None = None,
) -> pd.DataFrame:
    return _df(
        """SELECT e.proximity_id, e.mmsi, v.vessel_name, p.port_name, e.start_time, e.end_time,
                         e.n_obs, ROUND(e.min_distance_m::numeric) AS min_distance_m
                  FROM port_proximity_events e JOIN ports p USING (port_id) JOIN vessels v USING (mmsi)
                  WHERE (CAST(:pid AS int) IS NULL OR e.port_id = :pid)
                    AND (CAST(:m AS text) IS NULL OR e.mmsi = :m) AND NOT v.is_synthetic
                  ORDER BY e.start_time DESC LIMIT :lim""",
        {"pid": port_id, "m": mmsi, "lim": min(limit, MAX_PAGE)},
        engine,
    )


def positions_in_bbox(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    limit: int = 20000,
    engine: Engine | None = None,
) -> pd.DataFrame:
    """Spatial filter with ST_Intersects on the geography index; capped for the browser."""
    min_lat, max_lat, min_lon, max_lon = bbox
    return _df(
        """SELECT p.position_id, p.mmsi, v.vessel_name, v.vessel_type, p.base_datetime, p.lat, p.lon, p.sog
                  FROM ais_positions p JOIN vessels v USING (mmsi)
                  WHERE ST_Intersects(p.geog, ST_MakeEnvelope(:x, :a, :y, :b, 4326)::geography)
                    AND p.base_datetime BETWEEN :s AND :e AND NOT v.is_synthetic
                  ORDER BY p.base_datetime LIMIT :lim""",
        {
            "a": min_lat,
            "b": max_lat,
            "x": min_lon,
            "y": max_lon,
            "s": start,
            "e": end,
            "lim": min(limit, 50000),
        },
        engine,
    )


def latest_positions(
    at: datetime, window_min: int = 30, engine: Engine | None = None
) -> pd.DataFrame:
    """Last known position per vessel in the window before `at` (DISTINCT ON)."""
    return _df(
        """SELECT DISTINCT ON (p.mmsi) p.mmsi, v.vessel_name, v.vessel_type, p.base_datetime,
                         p.lat, p.lon, p.sog, p.cog
                  FROM ais_positions p JOIN vessels v USING (mmsi)
                  WHERE p.base_datetime BETWEEN CAST(:t AS timestamptz) - make_interval(mins => :w) AND :t
                    AND NOT v.is_synthetic
                  ORDER BY p.mmsi, p.base_datetime DESC""",
        {"t": at, "w": window_min},
        engine,
    )


# ---------------------------------------------------------------- anomalies
def anomalies(
    anomaly_types: list[str] | None = None,
    detector: str | None = None,
    mmsi: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    include_synthetic: bool = False,
    synthetic_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    engine: Engine | None = None,
) -> tuple[pd.DataFrame, int]:
    limit = max(1, min(limit, MAX_PAGE))
    params = {
        "types": anomaly_types or ["__all__"],
        "all_types": not anomaly_types,
        "det": detector,
        "m": mmsi,
        "s": start,
        "e": end,
        "syn": include_synthetic or synthetic_only,
        "syn_only": synthetic_only,
        "lim": limit,
        "off": max(offset, 0),
    }
    where = f"""(:all_types OR a.anomaly_type IN :types)
        AND (CAST(:det AS text) IS NULL OR a.detector = :det)
        AND (CAST(:m AS text) IS NULL OR a.mmsi = :m)
        AND (CAST(:s AS timestamptz) IS NULL OR a.event_time >= :s)
        AND (CAST(:e AS timestamptz) IS NULL OR a.event_time <= :e)
        AND (:syn OR NOT v.is_synthetic) AND (NOT :syn_only OR v.is_synthetic)
        AND a.run_key IN (SELECT run_key FROM {_LATEST_KEYS})"""
    total = int(
        _df(
            f"SELECT COUNT(*) AS n FROM anomaly_events a JOIN vessels v USING (mmsi) WHERE {where}",
            params,
            engine,
            expanding=("types",),
        ).n[0]
    )
    df = _df(
        f"""SELECT a.event_id, a.run_key, a.detector, a.anomaly_type, a.mmsi, v.vessel_name,
                        a.event_time, a.end_time, a.lat, a.lon, a.score, a.explanation, a.quality_notes,
                        cardinality(a.supporting_position_ids) AS n_support, v.is_synthetic
                 FROM anomaly_events a JOIN vessels v USING (mmsi) WHERE {where}
                 ORDER BY a.score DESC NULLS LAST, a.event_time DESC LIMIT :lim OFFSET :off""",
        params,
        engine,
        expanding=("types",),
    )
    return df, total


def anomaly_detail(event_id: int, engine: Engine | None = None) -> dict[str, Any] | None:
    df = _df(
        """SELECT a.*, v.vessel_name, v.is_synthetic, m.model_version
                FROM anomaly_events a JOIN vessels v USING (mmsi)
                LEFT JOIN model_runs m USING (model_run_id) WHERE a.event_id = :e""",
        {"e": event_id},
        engine,
    )
    if df.empty:
        return None
    rec = df.iloc[0].to_dict()
    ids = list(rec.get("supporting_position_ids") or [])
    rec["supporting_positions"] = (
        _df(
            """SELECT position_id, mmsi, base_datetime, lat, lon, sog, cog, heading, nav_status, quality_flags
           FROM ais_positions WHERE position_id = ANY(:ids) ORDER BY base_datetime""",
            {"ids": ids},
            engine,
        )
        if ids
        else pd.DataFrame()
    )
    return rec


def anomaly_summary(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        f"""SELECT a.detector, a.anomaly_type, COUNT(*) AS events, COUNT(DISTINCT a.mmsi) AS vessels,
                         MIN(a.event_time) AS first, MAX(a.event_time) AS last
                  FROM anomaly_events a JOIN vessels v USING (mmsi)
                  WHERE NOT v.is_synthetic AND a.run_key IN (SELECT run_key FROM {_LATEST_KEYS})
                  GROUP BY 1, 2 ORDER BY events DESC""",
        engine=engine,
    )


def anomaly_types(engine: Engine | None = None) -> list[str]:
    return _df(
        "SELECT DISTINCT anomaly_type FROM anomaly_events ORDER BY 1", engine=engine
    ).anomaly_type.tolist()


def model_runs(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT model_run_id, model_name, model_version, n_train, n_eval, train_start, train_end,
                         eval_start, eval_end, data_hash, artifact_path, created_at, params, metrics
                  FROM model_runs ORDER BY created_at DESC""",
        engine=engine,
    )


def data_sources(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT source_id, name, publisher, url, access_date, coverage_start, coverage_end,
                         license, is_synthetic, notes FROM data_sources ORDER BY source_id""",
        engine=engine,
    )


def table_counts(engine: Engine | None = None) -> pd.DataFrame:
    return _df(
        """SELECT 'vessels' AS table_name, COUNT(*) AS n_rows FROM vessels
                  UNION ALL SELECT 'ais_positions', COUNT(*) FROM ais_positions
                  UNION ALL SELECT 'ports', COUNT(*) FROM ports
                  UNION ALL SELECT 'voyages', COUNT(*) FROM voyages
                  UNION ALL SELECT 'port_proximity_events', COUNT(*) FROM port_proximity_events
                  UNION ALL SELECT 'anomaly_events', COUNT(*) FROM anomaly_events
                  UNION ALL SELECT 'model_runs', COUNT(*) FROM model_runs
                  UNION ALL SELECT 'investigation_cases', COUNT(*) FROM investigation_cases
                  UNION ALL SELECT 'case_evidence', COUNT(*) FROM case_evidence
                  UNION ALL SELECT 'ingestion_runs', COUNT(*) FROM ingestion_runs
                  UNION ALL SELECT 'data_sources', COUNT(*) FROM data_sources""",
        engine=engine,
    )
