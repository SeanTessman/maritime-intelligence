"""Explainable, configurable rule-based detectors.

Each rule returns event dicts with the evidence needed to review it: the supporting
position ids, the measured values, the threshold that was exceeded, and data-quality
notes. Thresholds live in config/thresholds.yaml and are documented in
docs/methodology.md. They are heuristics for this study area, not standards.

A rule firing means "this deviates from a simple expectation". It is never, by
itself, evidence of wrongdoing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from maritime.ingestion.validate_ais import QualityFlag
from maritime.processing.vessel_features import vessel_type_group

RULE_TYPES = (
    "high_reported_speed",
    "speed_discrepancy",
    "reporting_gap",
    "abrupt_course_change",
    "position_jump",
    "extended_loitering",
    "peer_deviation",
)

SOG_CEILING = 102.2  # AIS encodes ">= 102.2 kn" as 102.2; values here are almost always errors


def _flags(df: pd.DataFrame, flag: QualityFlag) -> pd.Series:
    if "quality_flags" not in df:
        return pd.Series(False, index=df.index)
    return (df["quality_flags"].fillna(0).astype(int) & int(flag)) > 0


def _pos_ids(rows: pd.DataFrame) -> list[int]:
    return [int(x) for x in rows["position_id"].tolist()] if "position_id" in rows else []


def _event(
    rule: str,
    rows: pd.DataFrame,
    values: dict[str, Any],
    threshold: dict[str, Any],
    notes: list[str],
) -> dict[str, Any]:
    first, last = rows.iloc[0], rows.iloc[-1]
    return {
        "detector": "rule",
        "anomaly_type": rule,
        "mmsi": first["mmsi"],
        "event_time": first["base_datetime"],
        "end_time": last["base_datetime"],
        "lat": float(first["lat"]),
        "lon": float(first["lon"]),
        "score": None,
        "rule_flags": {"rule": rule, "values": values, "threshold": threshold},
        "supporting_position_ids": _pos_ids(rows),
        "quality_notes": "; ".join(notes) if notes else None,
    }


def _runs(mask: pd.Series, by: pd.Series) -> pd.Series:
    """Label consecutive True runs within each group; False rows get -1."""
    starts = mask & ~mask.groupby(by).shift(1, fill_value=False)
    run_id = starts.cumsum()
    return run_id.where(mask, -1)


def rule_high_reported_speed(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    thr_by_group = cfg["high_speed_kn"]
    groups = steps["vessel_type"].map(vessel_type_group) if "vessel_type" in steps else "other"
    thr = pd.Series(groups, index=steps.index).map(thr_by_group).fillna(thr_by_group["other"])
    mask = (steps["sog"] > thr) & ~_flags(steps, QualityFlag.SOG_UNAVAILABLE)
    runs = _runs(mask, steps["mmsi"])
    events = []
    for _rid, rows in steps[runs >= 0].groupby(runs[runs >= 0]):
        if len(rows) < cfg["high_speed_min_consecutive"]:
            continue
        notes = []
        if (rows["sog"] >= SOG_CEILING - 0.05).any():  # float4 storage: 102.2 -> 102.1999...
            notes.append(
                "SOG at the AIS 102.2 kn ceiling: almost certainly a transmitter or "
                "decoding error, not real speed"
            )
        impl = rows["implied_kn"].median()
        if pd.notna(impl) and impl < 0.5 * rows["sog"].median():
            notes.append(
                f"implied speed between fixes (median {impl:.1f} kn) does not support "
                "the reported speed"
            )
        events.append(
            _event(
                "high_reported_speed",
                rows,
                {
                    "max_sog_kn": float(rows["sog"].max()),
                    "n_fixes": len(rows),
                    "vessel_type_group": str(groups.loc[rows.index[0]])
                    if hasattr(groups, "loc")
                    else "other",
                },
                {
                    "sog_kn_gt": float(thr.loc[rows.index[0]]),
                    "min_consecutive": cfg["high_speed_min_consecutive"],
                },
                notes,
            )
        )
    return events


def rule_speed_discrepancy(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    dt = steps["dt_s"]
    valid = (
        dt.between(cfg["speed_discrepancy_min_dt_s"], cfg["speed_discrepancy_max_dt_s"])
        & (steps["step_m"] > cfg["speed_discrepancy_min_distance_m"])
        & steps["sog"].notna()
        & ~steps["is_jump"].fillna(False)
    )
    impl, sog = steps["implied_kn"], steps["sog"]
    diff = (impl - sog).abs()
    ratio = np.maximum(impl, sog) / np.maximum(np.minimum(impl, sog), 0.1)
    mask = valid & (diff > cfg["speed_discrepancy_kn"]) & (ratio > cfg["speed_discrepancy_ratio"])
    events = []
    for idx in steps.index[mask]:
        pos = steps.index.get_loc(idx)
        rows = steps.iloc[[pos - 1, pos]]
        events.append(
            _event(
                "speed_discrepancy",
                rows,
                {
                    "reported_sog_kn": float(sog[idx]),
                    "implied_kn": round(float(impl[idx]), 2),
                    "dt_s": float(dt[idx]),
                    "distance_m": round(float(steps.at[idx, "step_m"]), 1),
                },
                {
                    "abs_diff_kn_gt": cfg["speed_discrepancy_kn"],
                    "ratio_gt": cfg["speed_discrepancy_ratio"],
                },
                [
                    "timestamps are receiver times; batching or clock offsets can inflate implied speed"
                ],
            )
        )
    return events


def rule_reporting_gap(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    prev_sog = steps.groupby("mmsi", sort=False)["sog"].shift(1)
    moving = (steps["sog"] >= cfg["reporting_gap_min_sog_kn"]) | (
        prev_sog >= cfg["reporting_gap_min_sog_kn"]
    )
    ctx = steps.get("gap_context", pd.Series(pd.NA, index=steps.index))
    mask = (steps["dt_s"] > cfg["reporting_gap_s"]) & moving & (ctx == "mid_region_unexplained")
    events = []
    for idx in steps.index[mask.fillna(False)]:
        pos = steps.index.get_loc(idx)
        rows = steps.iloc[[pos - 1, pos]]
        events.append(
            _event(
                "reporting_gap",
                rows,
                {
                    "gap_h": round(float(steps.at[idx, "dt_s"]) / 3600, 2),
                    "distance_across_gap_km": round(float(steps.at[idx, "step_m"]) / 1000, 2),
                    "gap_context": "mid_region_unexplained",
                },
                {"gap_s_gt": cfg["reporting_gap_s"], "min_sog_kn": cfg["reporting_gap_min_sog_kn"]},
                [
                    "an observation gap is NOT evidence of deliberate AIS deactivation; receiver "
                    "coverage, terrestrial-station outages and data-pipeline gaps are common causes"
                ],
            )
        )
    return events


def rule_abrupt_course_change(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    prev_sog = steps.groupby("mmsi", sort=False)["sog"].shift(1)
    mask = (
        (steps["course_change_deg"] > cfg["course_change_deg"])
        & (steps["dt_s"] <= cfg["course_change_max_dt_s"])
        & (steps["sog"] >= cfg["course_change_min_sog_kn"])
        & (prev_sog >= cfg["course_change_min_sog_kn"])
        & ~_flags(steps, QualityFlag.COG_UNAVAILABLE)
    )
    runs = _runs(mask.fillna(False), steps["mmsi"])
    events = []
    for _, rows in steps[runs >= 0].groupby(runs[runs >= 0]):
        pos = steps.index.get_loc(rows.index[0])
        ev_rows = pd.concat([steps.iloc[[pos - 1]], rows])
        events.append(
            _event(
                "abrupt_course_change",
                ev_rows,
                {
                    "max_course_change_deg": round(float(rows["course_change_deg"].max()), 1),
                    "n_fixes": len(rows),
                    "sog_kn": float(rows["sog"].median()),
                },
                {
                    "course_change_deg_gt": cfg["course_change_deg"],
                    "max_dt_s": cfg["course_change_max_dt_s"],
                    "min_sog_kn": cfg["course_change_min_sog_kn"],
                },
                ["COG is noisy for slow or turning vessels and for some Class B units"],
            )
        )
    return events


def rule_position_jump(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    mask = (steps["implied_kn"] > cfg["position_jump_implied_kn"]) & (
        steps["step_m"] > cfg["position_jump_min_distance_m"]
    )
    events = []
    for idx in steps.index[mask.fillna(False)]:
        pos = steps.index.get_loc(idx)
        rows = steps.iloc[[pos - 1, pos]]
        events.append(
            _event(
                "position_jump",
                rows,
                {
                    "implied_kn": round(float(steps.at[idx, "implied_kn"]), 1),
                    "distance_km": round(float(steps.at[idx, "step_m"]) / 1000, 2),
                    "dt_s": float(steps.at[idx, "dt_s"]),
                },
                {
                    "implied_kn_gt": cfg["position_jump_implied_kn"],
                    "min_distance_m": cfg["position_jump_min_distance_m"],
                },
                [
                    "most likely a GNSS/decoding error or two transmitters sharing one MMSI; treat as "
                    "a data-quality event first"
                ],
            )
        )
    return events


def rule_extended_loitering(steps: pd.DataFrame, cfg: dict) -> list[dict]:
    slow = (steps["sog"] <= cfg["loiter_max_sog_kn"]) & (
        steps["port_dist_m"] > cfg["loiter_min_port_distance_m"]
    )
    slow &= ~steps["is_gap_start"].fillna(False)
    runs = _runs(slow.fillna(False), steps["mmsi"])
    events = []
    skip = set(cfg.get("loiter_skip_nav_status", []))
    for _, rows in steps[runs >= 0].groupby(runs[runs >= 0]):
        dur = (rows["base_datetime"].iloc[-1] - rows["base_datetime"].iloc[0]).total_seconds()
        if dur < cfg["loiter_min_duration_s"]:
            continue
        if "nav_status" in rows and rows["nav_status"].isin(skip).mean() > 0.5:
            continue  # self-reported at anchor / moored: a normal reason to be stationary
        sample = rows.iloc[np.linspace(0, len(rows) - 1, min(len(rows), 25)).astype(int)]
        ev = _event(
            "extended_loitering",
            sample,
            {
                "duration_h": round(dur / 3600, 2),
                "median_sog_kn": float(rows["sog"].median()),
                "min_port_distance_km": round(float(rows["port_dist_m"].min()) / 1000, 2),
                "n_fixes": len(rows),
            },
            {
                "max_sog_kn": cfg["loiter_max_sog_kn"],
                "min_duration_s": cfg["loiter_min_duration_s"],
                "min_port_distance_m": cfg["loiter_min_port_distance_m"],
            },
            [
                "WPI ports are single points; designated anchorages, terminals and marinas not "
                "in the WPI will look like 'away from port'"
            ],
        )
        ev["end_time"] = rows["base_datetime"].iloc[-1]
        events.append(ev)
    return events


def rule_peer_deviation(tracks: pd.DataFrame, cfg: dict) -> list[dict]:
    """Track-level robust z-score versus same vessel-type group (median / MAD).

    One event per track; if several features deviate, the most extreme one is the
    headline and the rest are listed under `other_features`.
    """
    hits: dict[int, list[dict]] = {}
    sizes: dict[str, int] = {}
    for group, g in tracks.groupby("vessel_type_group"):
        if len(g) < cfg["peer_min_group_size"]:
            continue
        sizes[group] = len(g)
        for feat in cfg["peer_features"]:
            x = g[feat]
            med = x.median()
            mad = (x - med).abs().median() * 1.4826
            if not np.isfinite(mad) or mad == 0:
                continue
            z = (x - med) / mad
            for idx in g.index[z.abs() > cfg["peer_robust_z"]]:
                hits.setdefault(idx, []).append(
                    {
                        "feature": feat,
                        "value": round(float(x[idx]), 3),
                        "group": group,
                        "group_median": round(float(med), 3),
                        "robust_z": round(float(z[idx]), 2),
                        "group_size": len(g),
                    }
                )
    events = []
    for idx, feats in hits.items():
        feats.sort(key=lambda d: abs(d["robust_z"]), reverse=True)
        t = tracks.loc[idx]
        events.append(
            {
                "detector": "rule",
                "anomaly_type": "peer_deviation",
                "mmsi": t["mmsi"],
                "event_time": t["start_time"],
                "end_time": t["end_time"],
                "lat": None,
                "lon": None,
                "score": None,
                "rule_flags": {
                    "rule": "peer_deviation",
                    "values": {**feats[0], "other_features": feats[1:]},
                    "threshold": {"abs_robust_z_gt": cfg["peer_robust_z"]},
                },
                "supporting_position_ids": [],
                "track_id": t["track_id"],
                "quality_notes": "vessel-type groups are coarse; AIS type codes are "
                "self-reported and sometimes wrong",
            }
        )
    return events


def _attach_track_evidence(events: list[dict], steps: pd.DataFrame, n: int = 50) -> None:
    """Give track-level events a sample of supporting fixes and a representative location."""
    for ev in events:
        if ev["supporting_position_ids"]:
            continue
        pts = steps[
            (steps["mmsi"] == ev["mmsi"])
            & (steps["base_datetime"] >= ev["event_time"])
            & (steps["base_datetime"] <= ev["end_time"])
        ]
        if pts.empty:
            continue
        sample = pts.iloc[np.linspace(0, len(pts) - 1, min(len(pts), n)).astype(int)]
        ev["supporting_position_ids"] = _pos_ids(sample)
        ev["lat"], ev["lon"] = float(pts["lat"].median()), float(pts["lon"].median())


POINT_RULES: dict[str, Callable[[pd.DataFrame, dict], list[dict]]] = {
    "high_reported_speed": rule_high_reported_speed,
    "speed_discrepancy": rule_speed_discrepancy,
    "reporting_gap": rule_reporting_gap,
    "abrupt_course_change": rule_abrupt_course_change,
    "position_jump": rule_position_jump,
    "extended_loitering": rule_extended_loitering,
}


def run_rules(steps: pd.DataFrame, tracks: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Apply every rule. `steps` must be sorted by mmsi/time with a RangeIndex."""
    steps = steps.reset_index(drop=True)
    events: list[dict] = []
    for fn in POINT_RULES.values():
        events.extend(fn(steps, cfg))
    peer = rule_peer_deviation(tracks, cfg)
    _attach_track_evidence(peer, steps)
    events.extend(peer)
    df = pd.DataFrame(events)
    if df.empty:
        return df
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df["end_time"] = pd.to_datetime(df["end_time"], utc=True)
    if "track_id" not in df:
        df["track_id"] = None
    missing = df["track_id"].isna()
    df.loc[missing, "track_id"] = (
        df.loc[missing, "mmsi"] + "_" + df.loc[missing, "event_time"].dt.strftime("%Y%m%d")
    )
    return df.sort_values(["event_time", "mmsi"]).reset_index(drop=True)
