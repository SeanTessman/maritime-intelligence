"""Trajectory reconstruction: ordering, de-duplication, step metrics, segmentation.

Input is a positions DataFrame with at least:
    mmsi, base_datetime (tz-aware UTC), lat, lon, sog, cog
and optionally position_id, heading, nav_status, quality_flags, vessel_type.

Reported speed (SOG, from the transponder) and implied speed (distance / time between
consecutive fixes) are kept in separate columns and never mixed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from maritime.config import BoundingBox
from maritime.utils.geo import MS_TO_KNOTS, angle_diff_deg, haversine_m, initial_bearing_deg

GAP_CONTEXTS = (
    "near_study_area_edge",  # vessel was close to the bbox edge: likely left the region
    "in_port",  # both ends near a port: berthing, coverage shadow, or Class B off
    "mid_region_unexplained",  # cause unknown: receiver coverage, data gaps, transmitter off...
)


@dataclass(frozen=True)
class TrajectoryParams:
    gap_threshold_s: float = 1800
    jump_break_implied_kn: float = 100
    jump_break_min_distance_m: float = 5000
    edge_margin_m: float = 3000
    port_margin_m: float = 3000

    @classmethod
    def from_config(cls, cfg: dict) -> TrajectoryParams:
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})


def prepare_positions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Sort by vessel/time, drop exact duplicates, resolve same-timestamp conflicts.

    For conflicting fixes (same MMSI and timestamp, different position) the fix
    closest to the vessel's previous kept position is retained. Returns the cleaned
    frame and counts of what was removed.
    """
    if df.empty:
        return df.copy(), {"exact_duplicates": 0, "timestamp_conflicts_dropped": 0}
    out = df.sort_values(["mmsi", "base_datetime"], kind="mergesort").reset_index(drop=True)
    n0 = len(out)
    out = out.drop_duplicates(subset=["mmsi", "base_datetime", "lat", "lon"], keep="first")
    out = out.reset_index(drop=True)
    exact = n0 - len(out)

    dup_ts = out.duplicated(subset=["mmsi", "base_datetime"], keep=False)
    conflicts_dropped = 0
    if dup_ts.any():
        # Distance of each fix to the previous fix with a *different* timestamp.
        prev_lat = out.groupby("mmsi")["lat"].shift(1)
        prev_lon = out.groupby("mmsi")["lon"].shift(1)
        same_ts_prev = out["base_datetime"].eq(out.groupby("mmsi")["base_datetime"].shift(1))
        prev_lat = prev_lat.mask(same_ts_prev).groupby(out["mmsi"]).ffill()
        prev_lon = prev_lon.mask(same_ts_prev).groupby(out["mmsi"]).ffill()
        d = haversine_m(
            out["lat"], out["lon"], prev_lat.fillna(out["lat"]), prev_lon.fillna(out["lon"])
        )
        out["_d"] = d
        keep_idx = out.loc[dup_ts].groupby(["mmsi", "base_datetime"])["_d"].idxmin()
        drop = out.index[dup_ts & ~out.index.isin(keep_idx)]
        conflicts_dropped = len(drop)
        out = out.drop(index=drop).drop(columns="_d")
    return out.reset_index(drop=True), {
        "exact_duplicates": int(exact),
        "timestamp_conflicts_dropped": int(conflicts_dropped),
    }


def add_step_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Per-fix metrics relative to the previous fix of the same vessel.

    dt_s, step_m, implied_kn, bearing_deg, course_change_deg (|ΔCOG|),
    sog_change_kn, accel_kn_per_min. The first fix of each vessel gets NaN.
    """
    out = df.copy()
    g = out.groupby("mmsi", sort=False)
    prev_t = g["base_datetime"].shift(1)
    prev_lat, prev_lon = g["lat"].shift(1), g["lon"].shift(1)
    out["dt_s"] = (out["base_datetime"] - prev_t).dt.total_seconds()
    out["step_m"] = haversine_m(prev_lat, prev_lon, out["lat"], out["lon"])
    with np.errstate(divide="ignore", invalid="ignore"):
        implied = out["step_m"] / out["dt_s"] * MS_TO_KNOTS
    out["implied_kn"] = implied.where(out["dt_s"] > 0)
    out["bearing_deg"] = initial_bearing_deg(prev_lat, prev_lon, out["lat"], out["lon"])
    out["course_change_deg"] = angle_diff_deg(g["cog"].shift(1), out["cog"])
    out["sog_change_kn"] = out["sog"] - g["sog"].shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["accel_kn_per_min"] = (out["sog_change_kn"] / (out["dt_s"] / 60.0)).where(
            out["dt_s"] > 0
        )
    return out


def segment_trajectories(df: pd.DataFrame, params: TrajectoryParams | None = None) -> pd.DataFrame:
    """Assign segment_id per vessel. A new segment starts after a long gap or a big jump.

    Requires add_step_metrics() output. Adds: is_gap_start, is_jump, segment_id.
    Plotting each segment separately prevents drawing a straight line across a
    period with no observations.
    """
    p = params or TrajectoryParams()
    out = df.copy()
    out["is_gap_start"] = out["dt_s"] > p.gap_threshold_s
    out["is_jump"] = (out["implied_kn"] > p.jump_break_implied_kn) & (
        out["step_m"] > p.jump_break_min_distance_m
    )
    brk = (out["is_gap_start"] | out["is_jump"]).astype(int)
    out["segment_id"] = brk.groupby(out["mmsi"], sort=False).cumsum().astype(int)
    return out


def distance_to_bbox_edge_m(lat: np.ndarray, lon: np.ndarray, bbox: BoundingBox) -> np.ndarray:
    """Approximate distance (m) from each point to the nearest edge of the bbox."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    d_s = haversine_m(lat, lon, np.full_like(lat, bbox.min_lat), lon)
    d_n = haversine_m(lat, lon, np.full_like(lat, bbox.max_lat), lon)
    d_w = haversine_m(lat, lon, lat, np.full_like(lon, bbox.min_lon))
    d_e = haversine_m(lat, lon, lat, np.full_like(lon, bbox.max_lon))
    return np.minimum.reduce([d_s, d_n, d_w, d_e])


def classify_gaps(
    df: pd.DataFrame,
    bbox: BoundingBox,
    port_dist_m: pd.Series | None,
    params: TrajectoryParams | None = None,
) -> pd.Series:
    """Label each gap-start fix with a context (see GAP_CONTEXTS); other rows get <NA>.

    This deliberately does NOT infer intent. "mid_region_unexplained" only means none
    of the benign structural explanations we can check applied.
    """
    p = params or TrajectoryParams()
    ctx = pd.Series(pd.NA, index=df.index, dtype="string")
    gaps = df.index[df["is_gap_start"].fillna(False)]
    if len(gaps) == 0:
        return ctx
    prev = df.groupby("mmsi", sort=False)[["lat", "lon"]].shift(1)
    edge_now = distance_to_bbox_edge_m(df.loc[gaps, "lat"], df.loc[gaps, "lon"], bbox)
    edge_prev = distance_to_bbox_edge_m(prev.loc[gaps, "lat"], prev.loc[gaps, "lon"], bbox)
    near_edge = (edge_now < p.edge_margin_m) | (edge_prev < p.edge_margin_m)
    if port_dist_m is not None:
        prev_port = port_dist_m.groupby(df["mmsi"], sort=False).shift(1)
        in_port = (port_dist_m.loc[gaps] < p.port_margin_m).to_numpy() & (
            prev_port.loc[gaps] < p.port_margin_m
        ).to_numpy()
    else:
        in_port = np.zeros(len(gaps), dtype=bool)
    labels = np.where(
        near_edge, "near_study_area_edge", np.where(in_port, "in_port", "mid_region_unexplained")
    )
    ctx.loc[gaps] = labels
    return ctx


def identity_collision_suspects(df: pd.DataFrame, min_jumps: int = 3) -> pd.Series:
    """MMSIs with repeated physically impossible jumps (possible shared/misconfigured MMSI).

    Returns a boolean Series indexed by MMSI. This is a data-quality indicator.
    """
    if "is_jump" not in df:
        raise ValueError("run segment_trajectories first")
    return df.groupby("mmsi")["is_jump"].sum() >= min_jumps


def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (mmsi, segment_id): times, counts, path length, endpoints."""
    if df.empty:
        return pd.DataFrame(
            columns=["mmsi", "segment_id", "start_time", "end_time", "n_obs", "distance_km"]
        )
    step = df["step_m"].where(~(df["is_gap_start"] | df["is_jump"]), 0.0).fillna(0.0)
    tmp = df.assign(_step=step)
    g = tmp.groupby(["mmsi", "segment_id"], sort=False)
    seg = g.agg(
        start_time=("base_datetime", "min"),
        end_time=("base_datetime", "max"),
        n_obs=("base_datetime", "size"),
        distance_m=("_step", "sum"),
        start_lat=("lat", "first"),
        start_lon=("lon", "first"),
        end_lat=("lat", "last"),
        end_lon=("lon", "last"),
    ).reset_index()
    # The first fix of a segment carries the gap that preceded it.
    first = tmp.groupby(["mmsi", "segment_id"], sort=False).head(1)
    seg["gap_before_s"] = first["dt_s"].where(first["is_gap_start"]).to_numpy()
    if "gap_context" in tmp:
        seg["gap_before_context"] = first["gap_context"].to_numpy()
    seg["distance_km"] = seg.pop("distance_m") / 1000.0
    return seg
