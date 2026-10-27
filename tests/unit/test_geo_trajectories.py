"""Geospatial math and trajectory reconstruction."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pyproj import Geod

from maritime.config import NY_HARBOR, BoundingBox
from maritime.processing.spatial_features import nearest_port, proximity_events
from maritime.processing.trajectories import (
    TrajectoryParams,
    add_step_metrics,
    build_segments,
    classify_gaps,
    identity_collision_suspects,
    prepare_positions,
    segment_trajectories,
)
from maritime.utils.geo import (
    angle_diff_deg,
    circular_std_deg,
    destination_point,
    haversine_m,
    initial_bearing_deg,
    normalize_lon,
)
from tests.conftest import make_positions

GEOD = Geod(ellps="WGS84")
CITY_PAIRS = [
    ((40.6413, -73.7781), (33.9416, -118.4085)),  # JFK - LAX
    ((40.7128, -74.0060), (51.5074, -0.1278)),  # New York - London
    ((40.60, -74.05), (40.70, -74.00)),  # within NY Harbor
    ((-33.87, 151.21), (-36.85, 174.76)),  # Sydney - Auckland
]


@pytest.mark.parametrize("a,b", CITY_PAIRS)
def test_haversine_matches_wgs84_geodesic_within_half_percent(a, b):
    _, _, ref = GEOD.inv(a[1], a[0], b[1], b[0])
    assert haversine_m(a[0], a[1], b[0], b[1]) == pytest.approx(ref, rel=0.005)


def test_haversine_zero_and_symmetry():
    assert haversine_m(40.7, -74.0, 40.7, -74.0) == 0
    assert haversine_m(40.0, -74.0, 41.0, -73.0) == pytest.approx(
        haversine_m(41.0, -73.0, 40.0, -74.0)
    )


def test_degree_of_longitude_is_not_a_degree_of_latitude():
    lat_deg = haversine_m(40.7, -74.0, 41.7, -74.0)
    lon_deg = haversine_m(40.7, -74.0, 40.7, -73.0)
    assert lat_deg == pytest.approx(111_195, rel=0.001)
    assert lon_deg == pytest.approx(111_195 * np.cos(np.radians(40.7)), rel=0.001)


def test_antimeridian_distance_is_short():
    # 179.9E to 179.9W is ~22 km at the equator, not ~40,000 km.
    assert haversine_m(0, 179.9, 0, -179.9) == pytest.approx(22_239, rel=0.001)


def test_longitude_normalization_and_angles():
    assert normalize_lon([190, -190, 180]).tolist() == [-170, 170, -180]
    assert angle_diff_deg(350, 10) == 20 and angle_diff_deg(0, 180) == 180
    assert initial_bearing_deg(40.0, -74.0, 41.0, -74.0) == pytest.approx(0, abs=1e-6)
    assert circular_std_deg([359, 1, 0]) < 2


def test_destination_point_round_trip():
    la, lo = destination_point(40.6, -74.0, 45.0, 10_000)
    assert haversine_m(40.6, -74.0, la, lo) == pytest.approx(10_000, rel=1e-6)


def test_prepare_sorts_and_resolves_conflicts():
    df = make_positions(
        [
            ("366000001", "2023-01-09T00:02:00", 40.602, -74.0, 5, 0),
            ("366000001", "2023-01-09T00:00:00", 40.600, -74.0, 5, 0),
            ("366000001", "2023-01-09T00:01:00", 40.601, -74.0, 5, 0),
            ("366000001", "2023-01-09T00:01:00", 40.900, -74.0, 5, 0),  # conflicting, far away
            ("366000001", "2023-01-09T00:00:00", 40.600, -74.0, 5, 0),  # exact duplicate
        ]
    )
    out, removed = prepare_positions(df)
    assert out["base_datetime"].is_monotonic_increasing
    assert removed == {"exact_duplicates": 1, "timestamp_conflicts_dropped": 1}
    assert 40.900 not in out["lat"].tolist()


def test_implied_speed_and_reported_speed_are_separate():
    # 1 nautical mile (1852 m) north in 6 minutes = 10 kn implied; transponder says 3 kn.
    la, lo = destination_point(40.6, -74.0, 0, 1852)
    df = make_positions(
        [
            ("366000001", "2023-01-09T00:00:00", 40.6, -74.0, 3, 0),
            ("366000001", "2023-01-09T00:06:00", la, lo, 3, 0),
        ]
    )
    s = add_step_metrics(df)
    assert s.loc[1, "implied_kn"] == pytest.approx(10.0, rel=1e-3)
    assert s.loc[1, "sog"] == 3
    assert np.isnan(s.loc[0, "implied_kn"])


def test_gap_segmentation_breaks_tracks():
    df = make_positions(
        [
            ("366000001", "2023-01-09T00:00:00", 40.60, -74.0, 5, 0),
            ("366000001", "2023-01-09T00:01:00", 40.601, -74.0, 5, 0),
            ("366000001", "2023-01-09T03:00:00", 40.65, -74.0, 5, 0),  # 3 h gap
            ("366000001", "2023-01-09T03:01:00", 40.651, -74.0, 5, 0),
            ("366000002", "2023-01-09T00:00:00", 40.70, -74.0, 5, 0),
        ]
    )
    s = segment_trajectories(add_step_metrics(prepare_positions(df)[0]), TrajectoryParams())
    assert s["segment_id"].tolist() == [0, 0, 1, 1, 0]
    seg = build_segments(s)
    assert len(seg) == 3
    # The 3-hour jump is NOT counted in the segment path length.
    assert seg.loc[(seg.mmsi == "366000001") & (seg.segment_id == 0), "distance_km"].item() < 0.2
    assert seg.loc[seg.segment_id == 1, "gap_before_s"].item() == pytest.approx(10740)


def test_impossible_jump_breaks_segment_and_flags_identity():
    rows = []
    for i in range(8):
        lat = 40.6 if i % 2 == 0 else 41.0  # ~44 km back and forth every minute
        rows.append(("366000009", f"2023-01-09T00:0{i}:00", lat, -74.0, 10, 0))
    s = segment_trajectories(add_step_metrics(make_positions(rows)), TrajectoryParams())
    assert s["is_jump"].sum() == 7
    assert identity_collision_suspects(s).loc["366000009"]


def test_gap_context_edge_port_and_unexplained():
    bbox = BoundingBox(min_lat=40.3, max_lat=41.1, min_lon=-74.4, max_lon=-73.4)
    df = make_positions(
        [
            ("A00000001", "2023-01-09T00:00:00", 40.31, -74.0, 5, 0),  # 1.1 km from south edge
            ("A00000001", "2023-01-09T02:00:00", 40.50, -74.0, 5, 0),
            ("B00000001", "2023-01-09T00:00:00", 40.70, -74.0, 0, 0),
            ("B00000001", "2023-01-09T02:00:00", 40.70, -74.0, 0, 0),
            ("C00000001", "2023-01-09T00:00:00", 40.70, -73.8, 5, 0),
            ("C00000001", "2023-01-09T02:00:00", 40.75, -73.8, 5, 0),
        ]
    )
    s = segment_trajectories(add_step_metrics(df))
    port_dist = pd.Series([9e9, 9e9, 100, 100, 9e9, 9e9], index=s.index)
    ctx = classify_gaps(s, bbox, port_dist)
    assert ctx.dropna().tolist() == ["near_study_area_edge", "in_port", "mid_region_unexplained"]


def test_nearest_port_and_proximity_events_merge_and_split():
    ports = pd.DataFrame({"port_id": [1, 2], "lat": [40.70, 40.60], "lon": [-74.00, -74.10]})
    pid, d = nearest_port(np.array([40.701]), np.array([-74.0]), ports)
    assert pid[0] == 1 and d[0] == pytest.approx(111, rel=0.01)
    df = make_positions(
        [
            ("366000001", "2023-01-09T00:00:00", 40.700, -74.0, 0, 0),
            ("366000001", "2023-01-09T00:10:00", 40.701, -74.0, 0, 0),
            ("366000001", "2023-01-09T03:00:00", 40.700, -74.0, 0, 0),  # > 1 h later: new event
            ("366000001", "2023-01-09T03:30:00", 40.650, -74.0, 9, 0),  # not near any port
        ]
    )
    ev = proximity_events(df, ports, radius_m=2500, merge_gap_s=3600)
    assert len(ev) == 2 and ev["n_obs"].tolist() == [2, 1]
    assert ev.loc[0, "position_ids"] == [1, 2]


def test_ny_harbor_bbox_config():
    assert NY_HARBOR.contains(40.7, -74.0) and not NY_HARBOR.contains(42.0, -74.0)
    with pytest.raises(ValueError):
        BoundingBox(min_lat=41, max_lat=40, min_lon=-74, max_lon=-73)
