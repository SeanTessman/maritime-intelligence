"""Plain-language explanations for detections.

Every explanation states (1) what was measured, (2) benign explanations that fit, and
(3) what extra evidence would be needed before drawing any conclusion.
"""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np
import pandas as pd

FEATURE_LABELS = {
    "mean_sog": "mean reported speed (kn)",
    "max_sog": "max reported speed (kn)",
    "std_sog": "speed variability (kn)",
    "p95_sog": "95th pct reported speed (kn)",
    "mean_abs_sog_change": "mean speed change between fixes (kn)",
    "max_abs_accel": "max acceleration (kn/min)",
    "mean_course_change": "mean course change (deg)",
    "p95_course_change": "95th pct course change (deg)",
    "max_implied_kn": "max implied speed (kn)",
    "p95_speed_discrepancy": "95th pct |implied - reported| speed (kn)",
    "route_length_km": "route length (km)",
    "route_efficiency": "route efficiency (0-1)",
    "heading_variation": "heading variation (deg)",
    "low_speed_frac": "fraction of fixes < 0.5 kn",
    "longest_low_speed_h": "longest low-speed period (h)",
    "min_port_dist_km": "closest approach to a WPI port (km)",
    "mean_port_dist_km": "mean distance to nearest WPI port (km)",
    "frac_near_port": "fraction of fixes near a port",
    "mean_density_log10": "traffic density (log10)",
    "max_gap_h": "longest reporting gap (h)",
    "coverage_continuity": "hourly coverage continuity",
    "n_jumps": "impossible position jumps",
}

RULE_TEXT = {
    "high_reported_speed": (
        "Reported speed over ground stayed above the vessel-type threshold for {n_fixes} "
        "consecutive fixes (max {max_sog_kn} kn).",
        "fast ferries and pilot boats; a wrong AIS ship-type code; SOG encoding errors",
        "the vessel's real type and service speed, and whether the implied speed between "
        "positions confirms the reported value",
    ),
    "speed_discrepancy": (
        "Reported speed ({reported_sog_kn} kn) disagrees with the speed implied by consecutive "
        "positions ({implied_kn} kn over {distance_m} m in {dt_s} s).",
        "GNSS position noise, receiver timestamp batching, or a stale SOG field",
        "fixes from other receivers or satellite AIS for the same interval",
    ),
    "reporting_gap": (
        "No observations for {gap_h} h while the vessel was moving, away from the study-area "
        "edge and ports; it moved {distance_across_gap_km} km across the gap.",
        "receiver coverage holes, station outages, data-feed gaps, or a powered-down unit",
        "coverage maps for the receiver network, satellite AIS, and whether other vessels in "
        "the same area also have gaps at that time",
    ),
    "abrupt_course_change": (
        "Course over ground changed by up to {max_course_change_deg} deg between fixes while "
        "reporting ~{sog_kn} kn.",
        "COG noise, manoeuvring in a channel, or a misconfigured heading/COG source",
        "the heading field, nearby traffic, and the channel geometry at that spot",
    ),
    "position_jump": (
        "Consecutive positions are {distance_km} km apart in {dt_s} s (implied {implied_kn} kn).",
        "GNSS or decoding error, or two transmitters configured with the same MMSI",
        "vessel static data (name, call sign, dimensions) on each side of the jump",
    ),
    "extended_loitering": (
        "Speed stayed below the loiter threshold for {duration_h} h at least "
        "{min_port_distance_km} km from any WPI port reference point.",
        "waiting at an anchorage, bunkering, survey or dredging work, or a terminal not in the WPI",
        "anchorage charts, the vessel's declared navigational status, and port call records",
    ),
    "peer_deviation": (
        "{feature} = {value} is a robust z-score of {robust_z} from the {group} group median "
        "({group_median}, n={group_size}).",
        "an unusual but routine schedule, a mislabeled vessel type, or a small peer group",
        "the vessel's actual role and comparable vessels' history over a longer period",
    ),
}


def explain_rule(ev: dict[str, Any]) -> str:
    rule = ev["anomaly_type"]
    vals = (ev.get("rule_flags") or {}).get("values", {})
    what, benign, needed = RULE_TEXT[rule]
    with contextlib.suppress(KeyError, IndexError):
        what = what.format(**vals)
    return (
        f"{what} Possible benign explanations: {benign}. To interpret this, review: "
        f"{needed}. This is a rule-based deviation, not evidence of illegal or hostile activity."
    )


def top_contributors(
    row: pd.Series, train_stats: dict[str, dict[str, float]], k: int = 3
) -> list[dict]:
    """Features furthest from the training median, in robust (IQR) units."""
    out = []
    for feat, st in train_stats.items():
        v = row.get(feat)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        scale = st["iqr"] if st["iqr"] > 0 else 1.0
        out.append(
            {
                "feature": feat,
                "value": float(v),
                "train_median": st["median"],
                "robust_dev": float((v - st["median"]) / scale),
            }
        )
    out.sort(key=lambda d: abs(d["robust_dev"]), reverse=True)
    return out[:k]


def explain_model(score: float, threshold: float, contributors: list[dict]) -> str:
    parts = [
        f"{FEATURE_LABELS.get(c['feature'], c['feature'])} = {c['value']:.2f} "
        f"(training median {c['train_median']:.2f})"
        for c in contributors
    ]
    return (
        f"Isolation Forest score {score:.3f} vs review threshold {threshold:.3f}. "
        f"Most unusual features: {'; '.join(parts)}. The score ranks how easily this "
        "vessel-day is isolated from training tracks; it is not a probability and does "
        "not indicate intent. Review the track, data-quality flags, and vessel type."
    )
