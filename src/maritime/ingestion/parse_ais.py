"""Stream NOAA Marine Cadastre AIS files (ZIP or CSV) in fixed-size chunks.

The 2023 daily file is ~900 MB of CSV (~7 million rows, nationwide), so it is never
loaded whole. Every column is read as text first; typing happens in `normalize_chunk`
so MMSI/IMO keep leading zeros and a malformed value in one row can't break a chunk.

Observed 2023 header (inspected 2026-10-19 from AIS_2023_01_09.zip):
MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,
Length,Width,Draft,Cargo,TransceiverClass
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

logger = logging.getLogger(__name__)

# Canonical column -> accepted source spellings (case-insensitive). Later NOAA releases
# have used snake_case/lowercase names, so both are accepted.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "mmsi": ("mmsi",),
    "base_datetime": ("basedatetime", "base_date_time", "datetime", "timestamp"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "longitude", "long"),
    "sog": ("sog", "speed_over_ground"),
    "cog": ("cog", "course_over_ground"),
    "heading": ("heading", "true_heading"),
    "vessel_name": ("vesselname", "vessel_name", "name"),
    "imo": ("imo",),
    "call_sign": ("callsign", "call_sign"),
    "vessel_type": ("vesseltype", "vessel_type"),
    "nav_status": ("status", "nav_status", "navigational_status"),
    "length_m": ("length",),
    "width_m": ("width",),
    "draft_m": ("draft",),
    "cargo": ("cargo",),
    "transceiver_class": ("transceiverclass", "transceiver_class", "transceiver"),
}
REQUIRED_COLUMNS = ("mmsi", "base_datetime", "lat", "lon")
CANONICAL_COLUMNS = tuple(COLUMN_ALIASES)
NUMERIC_COLUMNS = ("lat", "lon", "sog", "cog", "heading", "length_m", "width_m", "draft_m")
INTEGER_COLUMNS = ("vessel_type", "nav_status", "cargo")
TEXT_COLUMNS = ("mmsi", "vessel_name", "imo", "call_sign", "transceiver_class")


def build_column_mapping(source_columns: list[str]) -> dict[str, str]:
    """Map source column names to canonical names. Unknown columns are ignored (and logged)."""
    lookup = {alias: canon for canon, aliases in COLUMN_ALIASES.items() for alias in aliases}
    mapping: dict[str, str] = {}
    for col in source_columns:
        key = col.strip().lower().replace(" ", "_")
        canon = lookup.get(key) or lookup.get(key.replace("_", ""))
        if canon and canon not in mapping.values():
            mapping[col] = canon
        elif not canon:
            logger.info("Ignoring unrecognized AIS column %r", col)
    missing = [c for c in REQUIRED_COLUMNS if c not in mapping.values()]
    if missing:
        raise ValueError(f"AIS file is missing required columns {missing}; got {source_columns}")
    return mapping


def _open_text_stream(path: Path) -> io.BufferedIOBase:
    """Return a binary stream of the CSV content, whether `path` is a .zip or a .csv."""
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        members = [m for m in zf.infolist() if m.filename.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Expected exactly one CSV inside {path.name}, found {len(members)}")
        return zf.open(members[0])  # type: ignore[return-value]
    return path.open("rb")


def iter_raw_chunks(path: Path, block_size: int = 32 << 20) -> Iterator[pd.DataFrame]:
    """Yield raw text-typed chunks with canonical column names.

    pyarrow's streaming reader splits on byte blocks, so for a given file and
    `block_size` the chunk boundaries are deterministic, which makes chunk-level
    resume after an interruption safe.
    """
    stream = _open_text_stream(path)
    # Read header to learn the columns, then force every column to string.
    header_reader = pacsv.open_csv(
        _open_text_stream(path), read_options=pacsv.ReadOptions(block_size=1 << 20)
    )
    source_cols = header_reader.schema.names
    header_reader.close()
    mapping = build_column_mapping(source_cols)
    reader = pacsv.open_csv(
        stream,
        read_options=pacsv.ReadOptions(block_size=block_size),
        convert_options=pacsv.ConvertOptions(
            column_types={c: pa.string() for c in source_cols},
            strings_can_be_null=True,
            null_values=[""],
        ),
    )
    try:
        for batch in reader:
            df = batch.to_pandas()
            df = df.rename(columns=mapping)[list(mapping.values())]
            for col in CANONICAL_COLUMNS:
                if col not in df.columns:
                    df[col] = None
            yield df[list(CANONICAL_COLUMNS)]
    finally:
        reader.close()
        stream.close()


def normalize_chunk(raw: pd.DataFrame) -> pd.DataFrame:
    """Type a raw chunk: numbers to float, codes to nullable ints, timestamps to UTC.

    Unparseable values become NaN/NaT (they are counted by validation, not dropped here).
    """
    df = pd.DataFrame(index=raw.index)
    for col in TEXT_COLUMNS:
        s = raw[col].astype("string").str.strip()
        df[col] = s.mask(s == "")
    # IMO is published as "IMO9348651"; keep it as text but drop the prefix.
    df["imo"] = df["imo"].str.replace(r"^IMO", "", regex=True).mask(lambda s: s.isin(["", "0"]))
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(raw[col], errors="coerce").astype("float64")
    for col in INTEGER_COLUMNS:
        df[col] = pd.to_numeric(raw[col], errors="coerce").round().astype("Int64")
    df["base_datetime"] = pd.to_datetime(
        raw["base_datetime"], errors="coerce", utc=True, format="ISO8601"
    )
    # Width/length of 0 means "not reported" in AIS static data.
    for col in ("length_m", "width_m"):
        df.loc[df[col] <= 0, col] = np.nan
    return df[list(CANONICAL_COLUMNS)]


def bbox_mask(df: pd.DataFrame, bbox: tuple[float, float, float, float]) -> pd.Series:
    """Boolean mask of rows inside (min_lat, max_lat, min_lon, max_lon). NaN coords are outside."""
    min_lat, max_lat, min_lon, max_lon = bbox
    return df["lat"].between(min_lat, max_lat) & df["lon"].between(min_lon, max_lon)
