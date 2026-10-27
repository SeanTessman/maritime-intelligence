"""Detection: rules on hand-built cases, Isolation Forest reproducibility, evaluation metrics."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from maritime.detection import isolation_forest as iforest
from maritime.detection.evaluation import binary_metrics
from maritime.detection.explanations import explain_rule
from maritime.detection.rules import run_rules
from maritime.processing.pipeline import compute_steps, load_thresholds
from maritime.processing.vessel_features import (
    MODEL_FEATURES,
    compute_track_features,
    vessel_type_group,
)
from maritime.synthetic import BenchmarkConfig, benchmark_ports, make_benchmark
from maritime.utils.geo import destination_point

CFG = load_thresholds()
PORTS = pd.DataFrame({"port_id": [1], "port_name": ["P"], "lat": [40.70], "lon": [-74.02]})


def _track(
    n=30,
    sog=10.0,
    start="2030-01-07T00:00:00",
    mmsi="999000001",
    vtype=70,
    lat0=40.5,
    lon0=-73.9,
    bearing=0.0,
    interval=60,
):
    rows, la, lo = [], lat0, lon0
    t = pd.Timestamp(start, tz="UTC").to_pydatetime()
    for _ in range(n):
        rows.append(
            {
                "mmsi": mmsi,
                "base_datetime": t,
                "lat": la,
                "lon": lo,
                "sog": sog,
                "cog": bearing,
                "vessel_type": vtype,
                "quality_flags": 0,
                "nav_status": 0,
            }
        )
        la, lo = destination_point(la, lo, bearing, sog * 1852 / 3600 * interval)
        t += timedelta(seconds=interval)
    df = pd.DataFrame(rows)
    df["position_id"] = range(1, len(df) + 1)
    return df


def _detect(df):
    steps, _ = compute_steps(df, PORTS, CFG)
    tracks = compute_track_features(steps)
    return run_rules(steps, tracks, CFG["rules"])


def test_normal_transit_triggers_no_rules():
    assert _detect(_track()).empty


def test_high_speed_rule_needs_consecutive_fixes():
    df = _track()
    df.loc[10, "sog"] = 50  # single spike: ignored
    assert "high_reported_speed" not in set(_detect(df).get("anomaly_type", []))
    df.loc[10:12, "sog"] = 50
    ev = _detect(df)
    hs = ev[ev.anomaly_type == "high_reported_speed"]
    assert len(hs) == 1 and hs.iloc[0]["rule_flags"]["values"]["n_fixes"] == 3
    assert "implied speed" in hs.iloc[0]["quality_notes"]


def test_position_jump_and_speed_discrepancy():
    df = _track()
    la, lo = destination_point(df.at[15, "lat"], df.at[15, "lon"], 90, 20_000)
    df.loc[15, ["lat", "lon"]] = [la, lo]
    types = set(_detect(df)["anomaly_type"])
    assert "position_jump" in types


def test_reporting_gap_only_mid_region_while_moving():
    a = _track(n=20, lat0=40.6, lon0=-73.8)
    b = _track(n=20, lat0=40.66, lon0=-73.8, start="2030-01-07T03:30:00")
    b["position_id"] += 100
    ev = _detect(pd.concat([a, b], ignore_index=True))
    gap = ev[ev.anomaly_type == "reporting_gap"]
    assert len(gap) == 1
    assert "NOT evidence of deliberate AIS deactivation" in gap.iloc[0]["quality_notes"]
    assert "not evidence of illegal or hostile activity" in explain_rule(gap.iloc[0].to_dict())


def test_loitering_respects_anchor_status_and_ports():
    df = _track(n=240, sog=0.2, lat0=40.45, lon0=-73.9, interval=60)  # 4 h, far from port
    assert "extended_loitering" in set(_detect(df)["anomaly_type"])
    df["nav_status"] = 1  # self-reported at anchor
    assert "extended_loitering" not in set(_detect(df).get("anomaly_type", []))
    near = _track(n=240, sog=0.2, lat0=40.701, lon0=-74.02)  # at the port point
    assert "extended_loitering" not in set(_detect(near).get("anomaly_type", []))


def test_course_change_rule():
    df = _track()
    df.loc[12, "cog"] = 180.0
    ev = _detect(df)
    assert "abrupt_course_change" in set(ev["anomaly_type"])


def test_vessel_type_groups():
    assert vessel_type_group(71) == "cargo" and vessel_type_group(52) == "tug_tow"
    assert vessel_type_group(None) == "other" and vessel_type_group(float("nan")) == "other"


@pytest.fixture(scope="module")
def bench():
    cfg = BenchmarkConfig(n_vessels=60, n_days=5, anomaly_rate=0.2, seed=5)
    pos, labels = make_benchmark(cfg)
    steps, _ = compute_steps(pos, benchmark_ports(), CFG)
    tracks = compute_track_features(steps).merge(
        labels, on=["track_id", "mmsi"], how="left", suffixes=("", "_l")
    )
    return tracks


def test_feature_schema_enforced(bench):
    assert set(MODEL_FEATURES) <= set(bench.columns)
    with pytest.raises(ValueError, match="missing columns"):
        iforest.validate_feature_frame(bench.drop(columns=["max_sog"]))


def test_isolation_forest_reproducible_and_serializable(bench, tmp_path):
    m1 = iforest.train(bench, CFG["isolation_forest"], seed=1, version="t1")
    m2 = iforest.train(bench, CFG["isolation_forest"], seed=1, version="t2")
    np.testing.assert_array_equal(m1.score(bench), m2.score(bench))
    m3 = iforest.train(bench, CFG["isolation_forest"], seed=2, version="t3")
    assert not np.array_equal(m1.score(bench), m3.score(bench))
    path = m1.save(tmp_path / "m.joblib")
    loaded = iforest.TrackModel.load(path)
    np.testing.assert_array_equal(loaded.score(bench), m1.score(bench))
    assert (tmp_path / "m.json").exists() and loaded.features == MODEL_FEATURES


def test_injected_anomalies_score_higher(bench):
    m = iforest.train(bench, CFG["isolation_forest"], seed=0)
    s = m.score(bench)
    anom = bench["is_anomaly"].fillna(False).astype(bool).to_numpy()
    assert s[anom].mean() > s[~anom].mean()


def test_time_split_has_no_leakage(bench):
    train, test = iforest.time_split(bench, date(2030, 1, 9))
    assert pd.to_datetime(train.track_date).max() < pd.to_datetime(test.track_date).min()
    assert set(train.track_id).isdisjoint(test.track_id)


def test_binary_metrics_known_values():
    m = binary_metrics(np.array([1, 1, 0, 0, 1]), np.array([1, 0, 1, 0, 1]))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 1)
    assert m["precision"] == pytest.approx(2 / 3, abs=1e-4) and m["recall"] == pytest.approx(
        2 / 3, abs=1e-4
    )
    assert m["false_positive_rate"] == 0.5
