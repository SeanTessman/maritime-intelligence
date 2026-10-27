"""Track-level features. The unit of analysis is a *vessel-day track* (MMSI x UTC date).

Vessel-days give a natural time split for evaluation (train on earlier days, score
later days) and keep one long-lived vessel from dominating the feature table.
Input: output of trajectories.add_step_metrics + segment_trajectories, with extra
columns `port_dist_m` (nearest-port distance) and `density_log10`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from maritime.utils.geo import circular_std_deg, haversine_m

FEATURE_COLUMNS: tuple[str, ...] = (
    # movement
    "mean_sog",
    "median_sog",
    "max_sog",
    "std_sog",
    "p95_sog",
    "mean_abs_sog_change",
    "max_abs_accel",
    "mean_course_change",
    "p95_course_change",
    "max_implied_kn",
    "p95_speed_discrepancy",
    # trajectory
    "n_obs",
    "duration_h",
    "route_length_km",
    "displacement_km",
    "route_efficiency",
    "heading_variation",
    "low_speed_frac",
    "longest_low_speed_h",
    # geospatial
    "min_port_dist_km",
    "mean_port_dist_km",
    "frac_near_port",
    "mean_density_log10",
    # reporting
    "obs_per_hour",
    "n_gaps",
    "max_gap_h",
    "coverage_continuity",
    "n_jumps",
)

# Subset used by the Isolation Forest (counts like n_obs are reporting artefacts and
# are excluded so the model is not just ranking "busy transmitters").
MODEL_FEATURES: tuple[str, ...] = (
    "mean_sog",
    "max_sog",
    "std_sog",
    "p95_sog",
    "mean_abs_sog_change",
    "max_abs_accel",
    "mean_course_change",
    "p95_course_change",
    "max_implied_kn",
    "p95_speed_discrepancy",
    "route_length_km",
    "route_efficiency",
    "heading_variation",
    "low_speed_frac",
    "longest_low_speed_h",
    "min_port_dist_km",
    "mean_port_dist_km",
    "frac_near_port",
    "mean_density_log10",
    "max_gap_h",
    "coverage_continuity",
    "n_jumps",
)

VESSEL_TYPE_GROUPS = {
    "fishing": range(30, 31),
    "tug_tow": [31, 32, 52],
    "pleasure_sailing": [36, 37],
    "passenger": range(60, 70),
    "cargo": range(70, 80),
    "tanker": range(80, 90),
}


def vessel_type_group(code: float | int | None) -> str:
    """Map an AIS ship-type code to a coarse group used for peer comparison."""
    if code is None or (isinstance(code, float) and np.isnan(code)) or pd.isna(code):
        return "other"
    c = int(code)
    for name, codes in VESSEL_TYPE_GROUPS.items():
        if c in codes:
            return name
    return "other"


def _longest_run_hours(times: pd.Series, mask: pd.Series) -> float:
    """Longest continuous stretch (hours) where mask is true, measured first->last fix."""
    if not mask.any():
        return 0.0
    run_id = (mask != mask.shift()).cumsum()
    best = 0.0
    for _, grp in times[mask].groupby(run_id[mask]):
        best = max(best, (grp.iloc[-1] - grp.iloc[0]).total_seconds() / 3600.0)
    return best


def _track_features(t: pd.DataFrame, gap_threshold_s: float, near_port_m: float) -> dict:
    sog = t["sog"].dropna()
    moving = t["sog"] >= 2.0
    valid_steps = ~(t["is_gap_start"].fillna(False) | t["is_jump"].fillna(False))
    steps = t["step_m"].where(valid_steps, 0.0).fillna(0.0)
    duration_h = (t["base_datetime"].iloc[-1] - t["base_datetime"].iloc[0]).total_seconds() / 3600
    route_km = steps.sum() / 1000.0
    disp_km = (
        float(haversine_m(t["lat"].iloc[0], t["lon"].iloc[0], t["lat"].iloc[-1], t["lon"].iloc[-1]))
        / 1000.0
    )
    dt = t["dt_s"]
    disc_mask = dt.between(30, 600) & t["sog"].notna() & (t["step_m"] > 50)
    disc = (t.loc[disc_mask, "implied_kn"] - t.loc[disc_mask, "sog"]).abs()
    hours = t["base_datetime"].dt.floor("h")
    span_hours = max(int((hours.iloc[-1] - hours.iloc[0]).total_seconds() // 3600) + 1, 1)
    cc = t.loc[moving & t["dt_s"].le(300), "course_change_deg"].dropna()
    pdist = t["port_dist_m"] if "port_dist_m" in t else pd.Series(np.nan, index=t.index)
    low = t["sog"] < 0.5
    return {
        "mean_sog": sog.mean() if len(sog) else np.nan,
        "median_sog": sog.median() if len(sog) else np.nan,
        "max_sog": sog.max() if len(sog) else np.nan,
        "std_sog": sog.std(ddof=0) if len(sog) else np.nan,
        "p95_sog": sog.quantile(0.95) if len(sog) else np.nan,
        "mean_abs_sog_change": t["sog_change_kn"].abs().mean(),
        "max_abs_accel": t.loc[dt.ge(30), "accel_kn_per_min"].abs().max(),
        "mean_course_change": cc.mean() if len(cc) else 0.0,
        "p95_course_change": cc.quantile(0.95) if len(cc) else 0.0,
        "max_implied_kn": t.loc[dt.ge(30), "implied_kn"].max(),
        "p95_speed_discrepancy": disc.quantile(0.95) if len(disc) else np.nan,
        "n_obs": len(t),
        "duration_h": duration_h,
        "route_length_km": route_km,
        "displacement_km": disp_km,
        "route_efficiency": disp_km / route_km if route_km > 0.5 else np.nan,
        "heading_variation": circular_std_deg(t.loc[moving, "cog"]),
        "low_speed_frac": float(low.mean()),
        "longest_low_speed_h": _longest_run_hours(t["base_datetime"], low),
        "min_port_dist_km": pdist.min() / 1000.0,
        "mean_port_dist_km": pdist.mean() / 1000.0,
        "frac_near_port": float((pdist <= near_port_m).mean()),
        "mean_density_log10": t["density_log10"].mean() if "density_log10" in t else np.nan,
        "obs_per_hour": len(t) / max(duration_h, 1 / 60),
        "n_gaps": int((dt > gap_threshold_s).sum()),
        "max_gap_h": float(np.nan_to_num(dt.max(), nan=0.0)) / 3600.0,
        "coverage_continuity": hours.nunique() / span_hours,
        "n_jumps": int(t["is_jump"].fillna(False).sum()),
    }


def compute_track_features(
    steps: pd.DataFrame, gap_threshold_s: float = 1800, near_port_m: float = 2500, min_obs: int = 2
) -> pd.DataFrame:
    """One row per vessel-day with FEATURE_COLUMNS plus identifiers and vessel_type_group.

    Step metrics are recomputed relative to the previous fix *on the same day* (the first
    fix of a day has no predecessor inside the track).
    """
    if steps.empty:
        return pd.DataFrame(columns=["track_id", "mmsi", "track_date", *FEATURE_COLUMNS])
    df = steps.copy()
    df["track_date"] = df["base_datetime"].dt.tz_convert("UTC").dt.date
    first_of_day = df["track_date"].ne(df.groupby("mmsi")["track_date"].shift(1))
    for col in (
        "dt_s",
        "step_m",
        "implied_kn",
        "course_change_deg",
        "sog_change_kn",
        "accel_kn_per_min",
    ):
        df.loc[first_of_day, col] = np.nan
    df.loc[first_of_day, ["is_gap_start", "is_jump"]] = False
    rows = []
    for (mmsi, day), t in df.groupby(["mmsi", "track_date"], sort=True):
        if len(t) < min_obs:
            continue
        feats = _track_features(t.reset_index(drop=True), gap_threshold_s, near_port_m)
        vt = (
            t["vessel_type"].dropna().iloc[0]
            if "vessel_type" in t and t["vessel_type"].notna().any()
            else None
        )
        rows.append(
            {
                "track_id": f"{mmsi}_{day:%Y%m%d}",
                "mmsi": mmsi,
                "track_date": day,
                "start_time": t["base_datetime"].iloc[0],
                "end_time": t["base_datetime"].iloc[-1],
                "vessel_type": vt,
                "vessel_type_group": vessel_type_group(vt),
                **feats,
            }
        )
    out = pd.DataFrame(rows)
    for col in FEATURE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    return out
