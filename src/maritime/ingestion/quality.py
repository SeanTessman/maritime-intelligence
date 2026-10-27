"""Data-quality accounting for one ingestion run.

The accumulator is serialisable (to_dict/from_dict) and saved to
ingestion_runs.quality_report after every committed chunk, so a resumed run continues
its report instead of starting over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from maritime.ingestion.validate_ais import ValidationResult

MISSINGNESS_FIELDS = (
    "sog",
    "cog",
    "heading",
    "vessel_name",
    "imo",
    "call_sign",
    "vessel_type",
    "nav_status",
    "length_m",
    "width_m",
    "draft_m",
    "cargo",
    "transceiver_class",
)


@dataclass
class QualityAccumulator:
    rows_raw: int = 0
    rows_invalid_coords_filewide: int = 0
    rows_outside_bbox: int = 0
    rows_in_bbox: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    duplicates_in_file: int = 0
    rows_inserted: int = 0
    reject_counts: dict[str, int] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)
    missing_counts: dict[str, int] = field(default_factory=dict)
    vessel_names: dict[str, list[str]] = field(default_factory=dict)
    time_min: str | None = None
    time_max: str | None = None
    lat_min: float | None = None
    lat_max: float | None = None
    lon_min: float | None = None
    lon_max: float | None = None

    def add_raw(self, n_raw: int, n_invalid_coords: int, n_outside: int, n_in: int) -> None:
        self.rows_raw += n_raw
        self.rows_invalid_coords_filewide += n_invalid_coords
        self.rows_outside_bbox += n_outside
        self.rows_in_bbox += n_in

    def add_missingness(self, normalized_bbox_rows: pd.DataFrame) -> None:
        for col in MISSINGNESS_FIELDS:
            self.missing_counts[col] = self.missing_counts.get(col, 0) + int(
                normalized_bbox_rows[col].isna().sum()
            )

    def add_validation(self, result: ValidationResult, inserted: int) -> None:
        acc = result.accepted
        self.rows_accepted += len(acc)
        self.rows_rejected += len(result.rejected)
        self.duplicates_in_file += result.duplicates_in_chunk
        self.rows_inserted += inserted
        for k, v in result.reject_counts.items():
            self.reject_counts[k] = self.reject_counts.get(k, 0) + v
        for k, v in result.flag_counts.items():
            self.flag_counts[k] = self.flag_counts.get(k, 0) + v
        if acc.empty:
            return
        names = acc.dropna(subset=["vessel_name"]).groupby("mmsi")["vessel_name"].unique()
        for mmsi in acc["mmsi"].unique():
            known = set(self.vessel_names.get(mmsi, []))
            if mmsi in names.index:
                known.update(names[mmsi])
            self.vessel_names[mmsi] = sorted(known)
        tmin, tmax = acc["base_datetime"].min().isoformat(), acc["base_datetime"].max().isoformat()
        self.time_min = min(filter(None, [self.time_min, tmin]))
        self.time_max = max(filter(None, [self.time_max, tmax]))
        self.lat_min = min(filter(lambda x: x is not None, [self.lat_min, float(acc.lat.min())]))
        self.lat_max = max(filter(lambda x: x is not None, [self.lat_max, float(acc.lat.max())]))
        self.lon_min = min(filter(lambda x: x is not None, [self.lon_min, float(acc.lon.min())]))
        self.lon_max = max(filter(lambda x: x is not None, [self.lon_max, float(acc.lon.max())]))

    @property
    def duplicates_vs_database(self) -> int:
        """Accepted rows that already existed in the DB (e.g. a re-run of the same file)."""
        return self.rows_accepted - self.rows_inserted

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> QualityAccumulator:
        if not d:
            return cls()
        known = {k: v for k, v in d.get("_state", d).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def report(self) -> dict[str, Any]:
        """Final, human-oriented report (also stored in the DB)."""
        multi_name = {m: n for m, n in self.vessel_names.items() if len(n) > 1}
        in_bbox = max(self.rows_in_bbox, 1)
        return {
            "total_raw_observations": self.rows_raw,
            "invalid_coordinates_filewide": self.rows_invalid_coords_filewide,
            "outside_study_region": self.rows_outside_bbox,
            "in_study_region": self.rows_in_bbox,
            "accepted": self.rows_accepted,
            "rejected": self.rows_rejected,
            "rejected_by_reason": self.reject_counts,
            "duplicates_within_file": self.duplicates_in_file,
            "duplicates_already_in_database": self.duplicates_vs_database,
            "inserted": self.rows_inserted,
            "quality_flags": self.flag_counts,
            "missing_by_field": self.missing_counts,
            "missing_pct_by_field": {
                k: round(100 * v / in_bbox, 2) for k, v in self.missing_counts.items()
            },
            "unique_vessels": len(self.vessel_names),
            "mmsi_with_multiple_names": len(multi_name),
            "time_coverage": {"min": self.time_min, "max": self.time_max},
            "geographic_coverage": {
                "lat_min": self.lat_min,
                "lat_max": self.lat_max,
                "lon_min": self.lon_min,
                "lon_max": self.lon_max,
            },
            "_state": self.to_dict(),
        }
