"""Geospatial features: nearest port, port proximity events, observation density, grid regions."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from maritime.utils.geo import EARTH_RADIUS_M

GRID_DEG = 0.1  # grid "regions" used for density and the network's region nodes


def nearest_port(
    lat: np.ndarray, lon: np.ndarray, ports: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Return (port_id, distance_m) of the nearest port for each point (haversine BallTree)."""
    if ports.empty:
        n = len(np.asarray(lat))
        return np.full(n, -1), np.full(n, np.inf)
    tree = BallTree(np.radians(ports[["lat", "lon"]].to_numpy()), metric="haversine")
    pts = np.radians(np.column_stack([np.asarray(lat, float), np.asarray(lon, float)]))
    dist, idx = tree.query(pts, k=1)
    return ports["port_id"].to_numpy()[idx[:, 0]], dist[:, 0] * EARTH_RADIUS_M


def proximity_events(
    df: pd.DataFrame, ports: pd.DataFrame, radius_m: float, merge_gap_s: float
) -> pd.DataFrame:
    """In-memory equivalent of the SQL proximity job (used for synthetic data and tests).

    A fix is "near" port P if P is the nearest port and within radius_m. Consecutive
    near-P fixes of one vessel merge into one event unless separated by > merge_gap_s.
    This is geographic proximity only; it does not establish that a vessel docked.
    """
    cols = [
        "mmsi",
        "port_id",
        "start_time",
        "end_time",
        "n_obs",
        "min_distance_m",
        "radius_m",
        "position_ids",
    ]
    if df.empty or ports.empty:
        return pd.DataFrame(columns=cols)
    pid, dist = nearest_port(df["lat"].to_numpy(), df["lon"].to_numpy(), ports)
    near = dist <= radius_m
    sub = df.loc[near, ["mmsi", "base_datetime"]].copy()
    sub["port_id"] = pid[near]
    sub["dist_m"] = dist[near]
    sub["position_id"] = df.loc[near, "position_id"].to_numpy() if "position_id" in df else -1
    if sub.empty:
        return pd.DataFrame(columns=cols)
    sub = sub.sort_values(["mmsi", "port_id", "base_datetime"])
    dt = sub.groupby(["mmsi", "port_id"])["base_datetime"].diff().dt.total_seconds()
    sub["event_no"] = (
        (dt.isna() | (dt > merge_gap_s)).astype(int).groupby([sub["mmsi"], sub["port_id"]]).cumsum()
    )
    ev = (
        sub.groupby(["mmsi", "port_id", "event_no"])
        .agg(
            start_time=("base_datetime", "min"),
            end_time=("base_datetime", "max"),
            n_obs=("base_datetime", "size"),
            min_distance_m=("dist_m", "min"),
            position_ids=("position_id", list),
        )
        .reset_index()
        .drop(columns="event_no")
    )
    ev["radius_m"] = float(radius_m)
    return ev[cols].sort_values(["mmsi", "start_time"]).reset_index(drop=True)


def grid_cell(lat: pd.Series, lon: pd.Series, size_deg: float = GRID_DEG) -> pd.Series:
    """Label a point with its grid region, e.g. 'G40.6_-74.1' (south-west corner)."""
    la = np.floor(np.asarray(lat, float) / size_deg) * size_deg
    lo = np.floor(np.asarray(lon, float) / size_deg) * size_deg
    return pd.Series([f"G{a:.1f}_{b:.1f}" for a, b in zip(la, lo, strict=True)], index=lat.index)


def density_lookup(df: pd.DataFrame, size_deg: float = 0.01) -> pd.Series:
    """log10(number of fixes) in each point's ~1 km grid cell, across all vessels."""
    key = (
        np.floor(df["lat"] / size_deg).astype(int).astype(str)
        + "_"
        + np.floor(df["lon"] / size_deg).astype(int).astype(str)
    )
    counts = key.map(key.value_counts())
    return np.log10(counts.astype(float))
