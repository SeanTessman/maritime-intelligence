"""Load NGA World Port Index (Pub. 150) port reference points near the study region.

Inspected 2026-10-19: UpdatedPub150.csv has 100+ columns; the ones used here are
'World Port Index Number', 'Main Port Name', 'Alternate Port Name', 'UN/LOCODE',
'Country Code' (holds the country *name*), 'Region Name', 'Harbor Size', 'Harbor Type',
'Latitude', 'Longitude'. Each port is a single reference point, not a berth polygon.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from maritime.config import BoundingBox, get_settings
from maritime.database import get_engine
from maritime.ingestion.load_database import ensure_source
from maritime.sources import NGA_WPI

logger = logging.getLogger(__name__)

WPI_COLUMNS = {
    "World Port Index Number": "wpi_number",
    "Main Port Name": "port_name",
    "Alternate Port Name": "alternate_name",
    "UN/LOCODE": "unlocode",
    "Country Code": "country",
    "Region Name": "region_name",
    "Harbor Size": "harbor_size",
    "Harbor Type": "harbor_type",
    "Latitude": "lat",
    "Longitude": "lon",
}


def parse_wpi(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in WPI_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"WPI file missing expected columns: {missing}")
    df = df[list(WPI_COLUMNS)].rename(columns=WPI_COLUMNS)
    for col in (
        "port_name",
        "alternate_name",
        "unlocode",
        "country",
        "region_name",
        "harbor_size",
        "harbor_type",
    ):
        df[col] = df[col].str.strip().replace({"": None})
    df["wpi_number"] = pd.to_numeric(df["wpi_number"], errors="coerce").astype("Int64")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    bad = df["lat"].isna() | df["lon"].isna() | df["port_name"].isna() | df["wpi_number"].isna()
    if bad.any():
        logger.warning("Dropping %d WPI rows with missing number/name/coordinates", int(bad.sum()))
    return df[~bad].reset_index(drop=True)


def filter_ports(df: pd.DataFrame, bbox: BoundingBox, buffer_deg: float) -> pd.DataFrame:
    return df[
        df["lat"].between(bbox.min_lat - buffer_deg, bbox.max_lat + buffer_deg)
        & df["lon"].between(bbox.min_lon - buffer_deg, bbox.max_lon + buffer_deg)
    ].reset_index(drop=True)


def load_wpi(
    path: Path | None = None,
    engine: Engine | None = None,
    bbox: BoundingBox | None = None,
    buffer_deg: float = 0.5,
) -> dict[str, int]:
    """Upsert ports within the study bbox (plus buffer). Idempotent on wpi_number."""
    settings = get_settings()
    path = path or settings.reference_dir / "UpdatedPub150.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Download it from {NGA_WPI.url}")
    allp = parse_wpi(path)
    ports = filter_ports(allp, bbox or settings.bbox, buffer_deg)
    engine = engine or get_engine()
    with engine.begin() as conn:
        sid = ensure_source(conn, NGA_WPI)
        for rec in ports.to_dict("records"):
            rec = {k: (None if pd.isna(v) else v) for k, v in rec.items()}
            rec["wpi_number"] = int(rec["wpi_number"])
            conn.execute(
                text(
                    """
                    INSERT INTO ports (wpi_number, port_name, alternate_name, unlocode, country,
                        region_name, harbor_size, harbor_type, lat, lon, source_id)
                    VALUES (:wpi_number, :port_name, :alternate_name, :unlocode, :country,
                        :region_name, :harbor_size, :harbor_type, :lat, :lon, :sid)
                    ON CONFLICT (wpi_number) DO UPDATE SET port_name = EXCLUDED.port_name,
                        alternate_name = EXCLUDED.alternate_name, lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon, harbor_size = EXCLUDED.harbor_size,
                        harbor_type = EXCLUDED.harbor_type
                    """
                ),
                {**rec, "sid": sid},
            )
    return {"wpi_rows_total": len(allp), "ports_loaded": len(ports)}
