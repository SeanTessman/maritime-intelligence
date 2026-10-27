"""SYNTHETIC data generators. Nothing produced here is NOAA data.

Three products, all deterministic for a given seed:

1. `make_fixture_csv`  - a small NOAA-schema CSV with deliberately bad rows, used by
   ingestion tests and CI (tests/fixtures/synthetic_ais_small.csv).
2. `make_benchmark`    - a labeled anomaly benchmark: normal harbour transits plus
   injected anomalies with known labels at the vessel-day level.
3. `make_network_scenario` - vessels visiting synthetic ports with a known
   two-community structure and one "bridge" vessel, for graph tests.

All synthetic MMSIs use the 9990xxxxx block (first digit 9 is not a ship-station MID,
so these can never collide with a real vessel's MMSI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from maritime.config import PROJECT_ROOT, get_settings
from maritime.utils.geo import destination_point, haversine_m, initial_bearing_deg

SYNTHETIC_MMSI_PREFIX = "9990"

# Waypoints roughly along navigable channels of New York Harbor (approximate).
WAYPOINTS: dict[str, tuple[float, float]] = {
    "ambrose": (40.455, -73.830),
    "lower_bay": (40.520, -73.990),
    "narrows": (40.607, -74.045),
    "upper_bay": (40.665, -74.045),
    "kvk_east": (40.645, -74.085),
    "kvk_west": (40.642, -74.140),
    "newark_bay": (40.680, -74.140),
    "port_newark": (40.695, -74.150),
    "hudson_mid": (40.760, -74.010),
    "hudson_north": (40.870, -73.945),
    "east_river_s": (40.700, -74.000),
    "east_river_n": (40.780, -73.935),
    "li_sound_w": (40.810, -73.790),
    "raritan_bay": (40.490, -74.150),
    "arthur_kill": (40.560, -74.235),
}

ROUTES: list[list[str]] = [
    [
        "ambrose",
        "lower_bay",
        "narrows",
        "upper_bay",
        "kvk_east",
        "kvk_west",
        "newark_bay",
        "port_newark",
    ],
    [
        "port_newark",
        "newark_bay",
        "kvk_west",
        "kvk_east",
        "upper_bay",
        "narrows",
        "lower_bay",
        "ambrose",
    ],
    ["narrows", "upper_bay", "hudson_mid", "hudson_north"],
    ["hudson_north", "hudson_mid", "upper_bay", "narrows"],
    ["upper_bay", "east_river_s", "east_river_n", "li_sound_w"],
    ["li_sound_w", "east_river_n", "east_river_s", "upper_bay"],
    ["lower_bay", "raritan_bay", "arthur_kill"],
    ["arthur_kill", "raritan_bay", "lower_bay", "narrows"],
]

VESSEL_PROFILES = {  # (AIS type code, mean speed kn, speed sd)
    "cargo": (71, 12.0, 1.5),
    "tanker": (81, 10.0, 1.2),
    "tug_tow": (52, 7.0, 1.0),
    "passenger": (60, 22.0, 3.0),
}

ANOMALY_TYPES = ("speed_spike", "position_jump", "reporting_gap", "loitering", "course_reversal")


@dataclass(frozen=True)
class BenchmarkConfig:
    n_vessels: int = 200
    n_days: int = 7
    start_date: datetime = datetime(2030, 1, 7, tzinfo=UTC)  # clearly not a real NOAA date
    anomaly_rate: float = 0.10
    interval_s: float = 60.0
    seed: int = 7


def _smooth(points: list[tuple[float, float]], iterations: int = 3) -> list[tuple[float, float]]:
    """Chaikin corner cutting so turns are gradual, like real vessel tracks."""
    pts = points
    for _ in range(iterations):
        out = [pts[0]]
        for (a1, b1), (a2, b2) in zip(pts[:-1], pts[1:], strict=True):
            out.append((0.75 * a1 + 0.25 * a2, 0.75 * b1 + 0.25 * b2))
            out.append((0.25 * a1 + 0.75 * a2, 0.25 * b1 + 0.75 * b2))
        out.append(pts[-1])
        pts = out
    return pts


def _interp_route(
    rng: np.random.Generator,
    route: list[str],
    speed_kn: float,
    start: datetime,
    interval_s: float,
    jitter_m: float = 15.0,
) -> pd.DataFrame:
    """Sample positions along a smoothed waypoint route at ~interval_s, constant-ish speed.

    The last fix lands exactly on the final waypoint so the following dwell starts
    where the transit ended (no artificial jump).
    """
    pts = _smooth([WAYPOINTS[w] for w in route])
    la = np.array([p[0] for p in pts])
    lo = np.array([p[1] for p in pts])
    seg_m = haversine_m(la[:-1], lo[:-1], la[1:], lo[1:])
    cum = np.concatenate([[0.0], np.cumsum(seg_m)])
    total = float(cum[-1])
    rows = []
    t, s_pos = start, 0.0
    while True:
        k = int(min(np.searchsorted(cum, s_pos, side="right") - 1, len(seg_m) - 1))
        brg = float(initial_bearing_deg(la[k], lo[k], la[k + 1], lo[k + 1]))
        plat, plon = destination_point(la[k], lo[k], brg, s_pos - cum[k])
        if s_pos < total:
            plat, plon = destination_point(
                plat, plon, rng.uniform(0, 360), abs(rng.normal(0, jitter_m))
            )
        sog = max(0.5, rng.normal(speed_kn, 0.4))
        rows.append((t, plat, plon, round(sog, 1), round((brg + rng.normal(0, 2)) % 360, 1) % 360))
        if s_pos >= total:
            break
        dt = interval_s + rng.normal(0, 3)
        step = sog * 1852 / 3600 * dt
        if s_pos + step >= total:  # arrive at the final waypoint
            dt *= (total - s_pos) / step
            s_pos = total
        else:
            s_pos += step
        t += timedelta(seconds=float(dt))
    return pd.DataFrame(rows, columns=["base_datetime", "lat", "lon", "sog", "cog"])


def _dwell(
    rng: np.random.Generator,
    at: tuple[float, float],
    start: datetime,
    minutes: float,
    interval_s: float,
) -> pd.DataFrame:
    n = max(int(minutes * 60 / interval_s), 1)
    times = [start + timedelta(seconds=i * interval_s + rng.normal(0, 3)) for i in range(n)]
    lat = at[0] + rng.normal(0, 0.0002, n)
    lon = at[1] + rng.normal(0, 0.0002, n)
    return pd.DataFrame(
        {
            "base_datetime": times,
            "lat": lat,
            "lon": lon,
            "sog": np.round(np.abs(rng.normal(0.1, 0.1, n)), 1),
            "cog": np.round(rng.uniform(0, 360, n), 1) % 360,
        }
    )


def _vessel_day(
    rng: np.random.Generator, mmsi: str, profile: str, day: datetime, interval_s: float
) -> pd.DataFrame:
    vtype, mean_kn, sd_kn = VESSEL_PROFILES[profile]
    route = ROUTES[rng.integers(len(ROUTES))]
    speed = max(3.0, rng.normal(mean_kn, sd_kn))
    t0 = day + timedelta(hours=float(rng.uniform(1, 10)))
    pre = _dwell(rng, WAYPOINTS[route[0]], t0, rng.uniform(30, 120), interval_s)
    leg = _interp_route(
        rng, route, speed, pre["base_datetime"].iloc[-1] + timedelta(seconds=interval_s), interval_s
    )
    post = _dwell(
        rng,
        WAYPOINTS[route[-1]],
        leg["base_datetime"].iloc[-1] + timedelta(seconds=interval_s),
        rng.uniform(30, 180),
        interval_s,
    )
    df = pd.concat([pre, leg, post], ignore_index=True)
    df["mmsi"] = mmsi
    df["vessel_type"] = vtype
    df["heading"] = df["cog"]
    return df


def _inject(rng: np.random.Generator, df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Inject one anomaly of `kind` into a single vessel-day track."""
    df = df.copy().reset_index(drop=True)
    moving = df.index[df["sog"] > 2].to_numpy()
    if len(moving) < 20:
        moving = df.index.to_numpy()
    i = int(rng.choice(moving[5:-5])) if len(moving) > 10 else len(df) // 2
    if kind == "speed_spike":
        df.loc[i : i + 4, "sog"] = np.round(rng.uniform(45, 60), 1)
    elif kind == "position_jump":
        la, lo = destination_point(
            df.at[i, "lat"], df.at[i, "lon"], rng.uniform(0, 360), rng.uniform(15_000, 30_000)
        )
        df.loc[i, ["lat", "lon"]] = [la, lo]
    elif kind == "reporting_gap":
        t_gap = df.at[i, "base_datetime"]
        df = df[~df["base_datetime"].between(t_gap, t_gap + timedelta(hours=3))]
        if len(df) < 20 or df["base_datetime"].max() < t_gap + timedelta(hours=3):
            # track too short to hold a mid-track gap: append a resumed leg after 3 h
            extra = df.tail(30).copy()
            extra["base_datetime"] = extra["base_datetime"] + timedelta(hours=3.5)
            df = pd.concat([df, extra], ignore_index=True)
    elif kind == "loitering":
        la, lo = df.at[i, "lat"], df.at[i, "lon"]
        t = df.at[i, "base_datetime"]
        loiter = _dwell(rng, (la, lo), t, 240, 60.0)
        loiter["sog"] = np.round(np.abs(rng.normal(0.4, 0.2, len(loiter))), 1)
        before = df[df["base_datetime"] < t]
        after = df[df["base_datetime"] >= t].copy()
        after["base_datetime"] = after["base_datetime"] + timedelta(hours=4, minutes=1)
        df = pd.concat(
            [
                before,
                loiter.assign(
                    mmsi=df.at[0, "mmsi"],
                    vessel_type=df.at[0, "vessel_type"],
                    heading=loiter["cog"],
                ),
                after,
            ],
            ignore_index=True,
        )
    elif kind == "course_reversal":
        for j in range(i, min(i + 6, len(df)), 2):
            df.loc[j, "cog"] = (df.at[j, "cog"] + 180) % 360
            df.loc[j, "sog"] = max(df.at[j, "sog"], 10.0)
            if j + 1 < len(df):
                df.loc[j + 1, "sog"] = max(df.at[j + 1, "sog"], 10.0)
    return df.sort_values("base_datetime").reset_index(drop=True)


def make_benchmark(cfg: BenchmarkConfig | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (positions, labels). labels: one row per vessel-day track with is_anomaly/kind."""
    cfg = cfg or BenchmarkConfig()
    rng = np.random.default_rng(cfg.seed)
    profiles = list(VESSEL_PROFILES)
    frames, labels = [], []
    for v in range(cfg.n_vessels):
        mmsi = f"{SYNTHETIC_MMSI_PREFIX}{10000 + v:05d}"
        profile = profiles[v % len(profiles)]
        for d in range(cfg.n_days):
            if rng.random() < 0.25:  # not every vessel moves every day
                continue
            day = cfg.start_date + timedelta(days=d)
            track = _vessel_day(rng, mmsi, profile, day, cfg.interval_s)
            kind = None
            if rng.random() < cfg.anomaly_rate:
                kind = ANOMALY_TYPES[rng.integers(len(ANOMALY_TYPES))]
                track = _inject(rng, track, kind)
            # keep each track inside its own UTC day so labels align with vessel-day tracks
            track = track[track["base_datetime"] < day + timedelta(days=1)]
            frames.append(track)
            labels.append(
                {
                    "track_id": f"{mmsi}_{day:%Y%m%d}",
                    "mmsi": mmsi,
                    "track_date": day.date(),
                    "is_anomaly": kind is not None,
                    "anomaly_kind": kind or "none",
                    "profile": profile,
                }
            )
    pos = pd.concat(frames, ignore_index=True)
    pos["base_datetime"] = pd.to_datetime(pos["base_datetime"], utc=True).dt.round("s")
    pos = pos.drop_duplicates(["mmsi", "base_datetime"]).sort_values(["mmsi", "base_datetime"])
    pos["position_id"] = np.arange(1, len(pos) + 1)
    pos["quality_flags"] = 0
    pos["nav_status"] = 0
    return pos.reset_index(drop=True), pd.DataFrame(labels)


def benchmark_ports() -> pd.DataFrame:
    """Synthetic 'ports' at route endpoints (for in-memory benchmark features)."""
    names = ["ambrose", "port_newark", "hudson_north", "li_sound_w", "arthur_kill", "narrows"]
    return pd.DataFrame(
        {
            "port_id": range(1, len(names) + 1),
            "port_name": names,
            "lat": [WAYPOINTS[n][0] for n in names],
            "lon": [WAYPOINTS[n][1] for n in names],
        }
    )


def make_fixture_csv(path: Path, seed: int = 11) -> Path:
    """Small NOAA-schema CSV with known bad rows. Returns the path written."""
    rng = np.random.default_rng(seed)
    day = datetime(2030, 1, 7, tzinfo=UTC)
    rows = []
    for k, profile in enumerate(["cargo", "tug_tow", "passenger", "tanker", "cargo"]):
        mmsi = f"{SYNTHETIC_MMSI_PREFIX}{k:05d}"
        df = _vessel_day(rng, mmsi, profile, day, 60.0).head(60)
        for r in df.itertuples():
            rows.append(
                {
                    "MMSI": mmsi,
                    "BaseDateTime": r.base_datetime.strftime("%Y-%m-%dT%H:%M:%S"),
                    "LAT": f"{r.lat:.5f}",
                    "LON": f"{r.lon:.5f}",
                    "SOG": r.sog,
                    "COG": r.cog,
                    "Heading": 511.0 if k == 1 else r.heading,
                    "VesselName": f"SYNTH VESSEL {k}",
                    "IMO": f"IMO99000{k:02d}" if k % 2 == 0 else "",
                    "CallSign": f"SYN{k}",
                    "VesselType": r.vessel_type,
                    "Status": 0,
                    "Length": 100 + k,
                    "Width": 20,
                    "Draft": "" if k == 3 else 8.0,
                    "Cargo": 70,
                    "TransceiverClass": "A",
                }
            )
    base = rows[0]
    bad = [
        {**base, "MMSI": "12345", "BaseDateTime": "2030-01-07T05:00:00"},  # bad MMSI
        {**base, "MMSI": "", "BaseDateTime": "2030-01-07T05:00:01"},  # missing MMSI
        {**base, "BaseDateTime": "not-a-time"},  # bad time
        {**base, "BaseDateTime": "2030-01-07T05:00:03", "LAT": "91.0", "LON": "181.0"},  # sentinel
        {**base, "BaseDateTime": "2030-01-07T05:00:04", "LAT": "0", "LON": "0"},  # null island
        {**base, "BaseDateTime": "2030-01-07T05:00:05", "LAT": "", "LON": ""},  # missing coords
        {**base, "BaseDateTime": "2030-01-07T05:00:06", "SOG": 102.3, "COG": 360.0},  # sentinels
        {
            **base,
            "BaseDateTime": "2030-01-07T05:00:07",
            "LAT": "34.0",
            "LON": "-118.2",
        },  # outside bbox
        rows[5],
        rows[6],  # exact duplicates
        {**rows[7], "LAT": f"{float(rows[7]['LAT']) + 0.01:.5f}"},  # conflicting time
    ]
    # Schema matches NOAA exactly; synthetic provenance is carried by the filename,
    # the 9990 MMSI block, the 2030 dates, and the data_sources registration.
    out = pd.DataFrame(rows + bad)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


def make_network_scenario(seed: int = 3) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Synthetic proximity events with a known structure.

    Group A vessels visit ports P1-P3, group B vessels visit ports P4-P6, and one bridge
    vessel visits P2 and P5. Expected: two communities joined only through the bridge,
    which has the highest betweenness among vessels.
    """
    rng = np.random.default_rng(seed)
    ports = pd.DataFrame(
        {
            "port_id": range(1, 7),
            "port_name": [f"SYN PORT {i}" for i in range(1, 7)],
            "lat": np.linspace(40.5, 40.9, 6),
            "lon": np.linspace(-74.2, -73.6, 6),
        }
    )
    t0 = datetime(2030, 1, 7, tzinfo=UTC)
    events = []
    pid = 0

    def add(mmsi: str, port: int, hour: float) -> None:
        nonlocal pid
        pid += 1
        start = t0 + timedelta(hours=hour)
        events.append(
            {
                "proximity_id": pid,
                "mmsi": mmsi,
                "port_id": port,
                "start_time": start,
                "end_time": start + timedelta(hours=1),
                "n_obs": 30,
                "min_distance_m": 400.0,
                "radius_m": 2500.0,
                "position_ids": [pid * 10, pid * 10 + 1],
            }
        )

    group_a = [f"{SYNTHETIC_MMSI_PREFIX}2{i:04d}" for i in range(5)]
    group_b = [f"{SYNTHETIC_MMSI_PREFIX}3{i:04d}" for i in range(5)]
    bridge = f"{SYNTHETIC_MMSI_PREFIX}40000"
    for m in group_a:
        for p in (1, 2, 3):
            add(m, p, float(rng.uniform(0, 48)))
    for m in group_b:
        for p in (4, 5, 6):
            add(m, p, float(rng.uniform(0, 48)))
    add(bridge, 2, 10.0)
    add(bridge, 5, 30.0)
    expected = {
        "group_a": group_a,
        "group_b": group_b,
        "bridge": bridge,
        "n_vessels": 11,
        "n_ports": 6,
        "n_communities": 2,
    }
    return pd.DataFrame(events), ports, expected


def write_synthetic_files(seed: int = 7) -> dict[str, Path]:
    """Write all synthetic products to data/synthetic and tests/fixtures."""
    out = get_settings().synthetic_dir
    out.mkdir(parents=True, exist_ok=True)
    pos, labels = make_benchmark(BenchmarkConfig(seed=seed))
    paths = {
        "benchmark_positions": out / "benchmark_positions.parquet",
        "benchmark_labels": out / "benchmark_labels.csv",
        "fixture_csv": PROJECT_ROOT / "tests" / "fixtures" / "synthetic_ais_small.csv",
    }
    pos.to_parquet(paths["benchmark_positions"], index=False)
    labels.to_csv(paths["benchmark_labels"], index=False)
    make_fixture_csv(paths["fixture_csv"])
    (out / "README.md").write_text(
        "# SYNTHETIC DATA\n\nEverything in this folder is generated by `src/maritime/synthetic.py` "
        "and is **not** NOAA data. Synthetic MMSIs use the 9990xxxxx block and timestamps in 2030 "
        "so they can never be mistaken for real observations.\n"
    )
    return paths


def load_synthetic_into_db() -> dict:
    """Load a small synthetic demo slice into the DB (flagged is_synthetic) for the
    investigation walkthrough. Uses the benchmark's anomalous tracks plus some normal ones."""
    from maritime.database import get_engine
    from maritime.ingestion.load_database import ensure_source, load_chunk
    from maritime.sources import SYNTHETIC_SCENARIO

    pos, labels = make_benchmark(BenchmarkConfig(seed=7))
    keep_tracks = set(labels.loc[labels.is_anomaly, "track_id"]) | set(
        labels.loc[~labels.is_anomaly, "track_id"].head(40)
    )
    pos["track_id"] = pos["mmsi"] + "_" + pos["base_datetime"].dt.strftime("%Y%m%d")
    demo = pos[pos["track_id"].isin(keep_tracks)].copy()
    for col in ("vessel_name", "imo", "call_sign", "transceiver_class"):
        demo[col] = None
    demo["vessel_name"] = "SYNTHETIC " + demo["mmsi"].str[-4:]
    demo["transceiver_class"] = "A"
    for col in ("length_m", "width_m", "draft_m", "cargo", "heading"):
        if col not in demo:
            demo[col] = np.nan
    from maritime.ingestion.validate_ais import validate_chunk

    for col in ("vessel_type", "nav_status", "cargo"):
        demo[col] = demo[col].astype("Int64")
    demo["mmsi"] = demo["mmsi"].astype("string")
    result = validate_chunk(demo.drop(columns=["quality_flags", "position_id", "track_id"]))
    engine = get_engine()
    with engine.begin() as conn:
        sid = ensure_source(conn, SYNTHETIC_SCENARIO)
        inserted = load_chunk(conn, result.accepted, sid, None, is_synthetic=True)
    return {"synthetic_positions_inserted": inserted, "tracks": len(keep_tracks)}
