"""Natural Earth 1:10m land/coastline, clipped to the study region for offline map context.

Source: https://www.naturalearthdata.com/downloads/ (public domain). Files downloaded
from https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip and
ne_10m_coastline.zip. 1:10m generalisation is coarse at harbour scale (narrow channels
and piers are not resolved); the layer is visual context only and is never used in
any calculation.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from maritime.config import PROJECT_ROOT, BoundingBox, get_settings

ASSET = PROJECT_ROOT / "assets" / "natural_earth_ny_land.geojson"


def build_land_asset(
    src_zip: Path | None = None,
    bbox: BoundingBox | None = None,
    buffer_deg: float = 0.6,
    out: Path = ASSET,
) -> Path:
    src_zip = src_zip or get_settings().reference_dir / "ne_10m_land.zip"
    b = bbox or get_settings().bbox
    land = gpd.read_file(f"zip://{src_zip}")
    clip = box(
        b.min_lon - buffer_deg,
        b.min_lat - buffer_deg,
        b.max_lon + buffer_deg,
        b.max_lat + buffer_deg,
    )
    clipped = gpd.clip(land.to_crs(4326), clip)[["geometry"]]
    clipped["source"] = "Natural Earth 1:10m land (public domain)"
    out.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(out, driver="GeoJSON")
    return out
