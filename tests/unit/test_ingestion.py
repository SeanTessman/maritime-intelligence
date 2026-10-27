"""Ingestion: column normalization, validation, MMSI handling, missing optional fields."""

from __future__ import annotations

import zipfile

import pandas as pd
import pytest

from maritime.ingestion.parse_ais import (
    CANONICAL_COLUMNS,
    bbox_mask,
    build_column_mapping,
    iter_raw_chunks,
    normalize_chunk,
)
from maritime.ingestion.validate_ais import (
    QualityFlag,
    classify_mmsi,
    describe_flags,
    validate_chunk,
)

NOAA_2023_HEADER = [
    "MMSI",
    "BaseDateTime",
    "LAT",
    "LON",
    "SOG",
    "COG",
    "Heading",
    "VesselName",
    "IMO",
    "CallSign",
    "VesselType",
    "Status",
    "Length",
    "Width",
    "Draft",
    "Cargo",
    "TransceiverClass",
]


def test_column_mapping_noaa_2023_header():
    m = build_column_mapping(NOAA_2023_HEADER)
    assert m["BaseDateTime"] == "base_datetime"
    assert m["Status"] == "nav_status"
    assert set(m.values()) == set(CANONICAL_COLUMNS)


def test_column_mapping_accepts_snake_case_variant():
    m = build_column_mapping(["mmsi", "base_date_time", "latitude", "longitude", "sog"])
    assert m == {
        "mmsi": "mmsi",
        "base_date_time": "base_datetime",
        "latitude": "lat",
        "longitude": "lon",
        "sog": "sog",
    }


def test_column_mapping_missing_required_raises():
    with pytest.raises(ValueError, match="missing required"):
        build_column_mapping(["MMSI", "LAT", "LON"])


def _raw(**overrides) -> pd.DataFrame:
    base = {c: None for c in CANONICAL_COLUMNS}
    base.update(
        mmsi="367799340",
        base_datetime="2023-01-09T00:00:01",
        lat="40.6",
        lon="-74.0",
        sog="6.5",
        cog="152.1",
        heading="159.0",
    )
    base.update(overrides)
    return pd.DataFrame([base])


def test_mmsi_and_imo_stay_strings_with_leading_zeros():
    df = normalize_chunk(_raw(mmsi="003669999", imo="IMO0012345"))
    assert df.loc[0, "mmsi"] == "003669999"
    assert df.loc[0, "imo"] == "0012345"
    assert df["mmsi"].dtype.name == "string"


def test_timestamps_normalized_to_utc():
    df = normalize_chunk(_raw())
    ts = df.loc[0, "base_datetime"]
    assert str(ts.tz) == "UTC" and ts.hour == 0 and ts.second == 1


def test_missing_optional_fields_are_null_not_errors():
    df = normalize_chunk(_raw(vessel_name="", draft_m=None, cargo=None, length_m="0"))
    assert pd.isna(df.loc[0, "vessel_name"]) and pd.isna(df.loc[0, "draft_m"])
    assert pd.isna(df.loc[0, "length_m"])  # 0 length means "not reported"
    res = validate_chunk(df)
    assert len(res.accepted) == 1 and len(res.rejected) == 0


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"lat": "91.0", "lon": "181.0"}, "invalid_coordinates"),
        ({"lat": "-95"}, "invalid_coordinates"),
        ({"lat": "0", "lon": "0"}, "zero_zero_position"),
        ({"lat": None}, "missing_coordinates"),
        ({"base_datetime": "garbage"}, "invalid_timestamp"),
        ({"mmsi": "12345"}, "invalid_mmsi_format"),
        ({"mmsi": None}, "missing_mmsi"),
    ],
)
def test_hard_rejections_have_reason_codes(overrides, reason):
    res = validate_chunk(normalize_chunk(_raw(**overrides)))
    assert len(res.accepted) == 0
    assert res.rejected["reject_reason"].tolist() == [reason]
    assert res.reject_counts[reason] == 1


def test_sentinel_values_become_null_with_flags():
    res = validate_chunk(normalize_chunk(_raw(sog="102.3", cog="360", heading="511")))
    row = res.accepted.iloc[0]
    assert pd.isna(row["sog"]) and pd.isna(row["cog"]) and pd.isna(row["heading"])
    names = describe_flags(int(row["quality_flags"]))
    assert {"SOG_UNAVAILABLE", "COG_UNAVAILABLE", "HEADING_UNAVAILABLE"} <= set(names)


def test_duplicates_removed_and_conflicts_flagged():
    raw = pd.concat([_raw(), _raw(), _raw(lat="40.7")], ignore_index=True)
    res = validate_chunk(normalize_chunk(raw))
    assert res.duplicates_in_chunk == 1
    assert len(res.accepted) == 2
    assert (res.accepted["quality_flags"] & int(QualityFlag.CONFLICTING_TIMESTAMP)).all()


def test_classify_mmsi_suspect_ranges():
    s = pd.Series(["367799340", "111111111", "993669999", "abc", None], dtype="string")
    missing, malformed, suspect = classify_mmsi(s)
    assert missing.tolist() == [False, False, False, False, True]
    assert malformed.tolist() == [False, False, False, True, False]
    assert suspect.tolist() == [False, True, True, False, False]


def test_bbox_mask_excludes_nan():
    df = pd.DataFrame({"lat": [40.5, 50.0, float("nan")], "lon": [-74.0, -74.0, -74.0]})
    assert bbox_mask(df, (40.3, 41.1, -74.4, -73.4)).tolist() == [True, False, False]


def test_fixture_reads_from_csv_and_zip(fixture_csv, tmp_path):
    csv_rows = sum(len(c) for c in iter_raw_chunks(fixture_csv, block_size=4096))
    zpath = tmp_path / "fixture.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(fixture_csv, arcname="AIS_SYNTH.csv")
    zip_rows = sum(len(c) for c in iter_raw_chunks(zpath, block_size=4096))
    assert csv_rows == zip_rows == len(pd.read_csv(fixture_csv))


def test_fixture_validation_counts(fixture_csv):
    df = pd.concat([normalize_chunk(c) for c in iter_raw_chunks(fixture_csv)], ignore_index=True)
    res = validate_chunk(df)
    rc = res.reject_counts
    assert rc["invalid_mmsi_format"] == 1 and rc["missing_mmsi"] == 1
    assert rc["invalid_timestamp"] == 1 and rc["invalid_coordinates"] == 1
    assert rc["zero_zero_position"] == 1 and rc["missing_coordinates"] == 1
    assert res.duplicates_in_chunk == 2
