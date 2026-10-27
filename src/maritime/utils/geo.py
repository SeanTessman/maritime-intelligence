"""Vectorised spherical geometry helpers.

Distances use the haversine formula on a sphere of mean Earth radius 6,371.0088 km.
Against the WGS84 ellipsoid (pyproj Geod) this is accurate to about 0.5%, which is
far below AIS position noise for harbour-scale work. Never use raw degree
differences as distances: at 40.7 N one degree of longitude is ~84 km, but one degree
of latitude is ~111 km.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

EARTH_RADIUS_M = 6_371_008.8
M_PER_NM = 1852.0
MS_TO_KNOTS = 3600.0 / M_PER_NM

ArrayLike = npt.ArrayLike


def haversine_m(lat1: ArrayLike, lon1: ArrayLike, lat2: ArrayLike, lon2: ArrayLike) -> np.ndarray:
    """Great-circle distance in metres. Works across the antimeridian (uses Δlon via sin)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def initial_bearing_deg(
    lat1: ArrayLike, lon1: ArrayLike, lat2: ArrayLike, lon2: ArrayLike
) -> np.ndarray:
    """Initial great-circle bearing from point 1 to point 2, in [0, 360)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    x = np.sin(dlmb) * np.cos(p2)
    y = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dlmb)
    return (np.degrees(np.arctan2(x, y)) + 360.0) % 360.0


def angle_diff_deg(a: ArrayLike, b: ArrayLike) -> np.ndarray:
    """Smallest absolute difference between two headings, in [0, 180]. 350 vs 10 -> 20."""
    d = np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)) % 360.0
    return np.minimum(d, 360.0 - d)


def normalize_lon(lon: ArrayLike) -> np.ndarray:
    """Wrap longitudes into [-180, 180)."""
    return ((np.asarray(lon, dtype=float) + 180.0) % 360.0) - 180.0


def circular_std_deg(angles: ArrayLike) -> float:
    """Circular standard deviation of headings (degrees). NaN for empty input."""
    a = np.radians(np.asarray(angles, dtype=float))
    a = a[~np.isnan(a)]
    if a.size == 0:
        return float("nan")
    r = np.hypot(np.mean(np.cos(a)), np.mean(np.sin(a)))
    r = min(max(r, 1e-12), 1.0)
    return float(np.degrees(np.sqrt(-2.0 * np.log(r))))


def destination_point(
    lat: float, lon: float, bearing_deg: float, distance_m: float
) -> tuple[float, float]:
    """Point reached travelling `distance_m` from (lat, lon) on an initial bearing."""
    d = distance_m / EARTH_RADIUS_M
    b = np.radians(bearing_deg)
    p1, l1 = np.radians(lat), np.radians(lon)
    p2 = np.arcsin(np.sin(p1) * np.cos(d) + np.cos(p1) * np.sin(d) * np.cos(b))
    l2 = l1 + np.arctan2(np.sin(b) * np.sin(d) * np.cos(p1), np.cos(d) - np.sin(p1) * np.sin(p2))
    return float(np.degrees(p2)), float(normalize_lon(np.degrees(l2)))
