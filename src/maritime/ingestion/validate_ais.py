"""Row-level AIS validation.

Two outcomes per row:

* **Rejected** (hard failure; the row can't be placed in space/time or tied to a vessel):
  bad MMSI format, unparseable timestamp, missing or impossible coordinates.
  Each rejected row gets a reason code and is counted; a sample is kept for review.
* **Accepted with quality flags** (soft issue; the position is usable but a field is
  unavailable or implausible). The bad field is set to NULL and a bit is set in
  `quality_flags`, so downstream detectors can tell "unknown" from "zero".

AIS sentinel values (ITU-R M.1371): SOG 102.3 = not available, COG 360 = not
available, heading 511 = not available, lat 91 / lon 181 = not available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag

import numpy as np
import pandas as pd


class QualityFlag(IntFlag):
    """Bitmask stored in ais_positions.quality_flags."""

    NONE = 0
    SOG_UNAVAILABLE = 1
    SOG_INVALID = 2
    COG_UNAVAILABLE = 4
    COG_INVALID = 8
    HEADING_UNAVAILABLE = 16
    HEADING_INVALID = 32
    MMSI_SUSPECT = 64  # well-formed but a known placeholder or non-ship MID range
    CONFLICTING_TIMESTAMP = 128  # same MMSI + timestamp reported at a different position


REJECT_REASONS = (
    "missing_mmsi",
    "invalid_mmsi_format",
    "invalid_timestamp",
    "missing_coordinates",
    "invalid_coordinates",
    "zero_zero_position",
)

PLACEHOLDER_MMSI = frozenset(
    {"000000000", "111111111", "123456789", "999999999", "987654321", "012345678"}
)


@dataclass
class ValidationResult:
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    reject_counts: dict[str, int] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)
    duplicates_in_chunk: int = 0


def classify_mmsi(mmsi: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (missing, malformed, suspect) boolean masks for an MMSI text column.

    Ship-station MMSIs are 9 digits whose first digit (start of the Maritime
    Identification Digits) is 2-7. Other leading digits are coast stations, SAR
    aircraft, aids to navigation, etc.; they are kept but flagged as suspect so
    vessel-behaviour models can exclude them.
    """
    missing = mmsi.isna()
    malformed = ~missing & ~mmsi.fillna("").str.fullmatch(r"[0-9]{9}")
    first = mmsi.fillna("0").str[0]
    suspect = ~missing & ~malformed & (mmsi.isin(PLACEHOLDER_MMSI) | ~first.isin(list("234567")))
    return missing, malformed, suspect


def validate_chunk(df: pd.DataFrame) -> ValidationResult:
    """Validate one normalized chunk (see parse_ais.normalize_chunk)."""
    df = df.copy()
    reason = pd.Series(pd.NA, index=df.index, dtype="string")

    missing_mmsi, malformed_mmsi, suspect_mmsi = classify_mmsi(df["mmsi"])
    reason = reason.mask(missing_mmsi & reason.isna(), "missing_mmsi")
    reason = reason.mask(malformed_mmsi & reason.isna(), "invalid_mmsi_format")
    reason = reason.mask(df["base_datetime"].isna() & reason.isna(), "invalid_timestamp")
    no_coord = df["lat"].isna() | df["lon"].isna()
    reason = reason.mask(no_coord & reason.isna(), "missing_coordinates")
    bad_coord = ~no_coord & ~(df["lat"].between(-90, 90) & df["lon"].between(-180, 180))
    reason = reason.mask(bad_coord & reason.isna(), "invalid_coordinates")
    zero = (df["lat"] == 0) & (df["lon"] == 0)
    reason = reason.mask(zero & reason.isna(), "zero_zero_position")

    rejected_mask = reason.notna()
    rejected = df[rejected_mask].assign(reject_reason=reason[rejected_mask])
    acc = df[~rejected_mask].copy()

    flags = np.zeros(len(acc), dtype=np.int64)
    flag_counts: dict[str, int] = {}

    def _flag(mask: pd.Series, flag: QualityFlag, null_col: str | None = None) -> None:
        nonlocal flags
        m = mask.fillna(False).to_numpy(dtype=bool)
        flags |= np.where(m, int(flag), 0)
        flag_counts[flag.name] = int(m.sum())
        if null_col is not None:
            acc.loc[m, null_col] = np.nan

    sog, cog, hdg = acc["sog"], acc["cog"], acc["heading"]
    _flag(sog.round(1) == 102.3, QualityFlag.SOG_UNAVAILABLE, "sog")
    _flag((sog < 0) | (sog > 102.3), QualityFlag.SOG_INVALID, "sog")
    _flag(cog.round(1) == 360.0, QualityFlag.COG_UNAVAILABLE, "cog")
    _flag((cog < 0) | (cog > 360), QualityFlag.COG_INVALID, "cog")
    _flag(hdg == 511, QualityFlag.HEADING_UNAVAILABLE, "heading")
    _flag(((hdg < 0) | (hdg >= 360)) & (hdg != 511), QualityFlag.HEADING_INVALID, "heading")
    _flag(suspect_mmsi[~rejected_mask], QualityFlag.MMSI_SUSPECT)

    acc["quality_flags"] = flags

    # Exact duplicates (same vessel, time, position) inside this chunk.
    key = ["mmsi", "base_datetime", "lat", "lon"]
    dup = acc.duplicated(subset=key, keep="first")
    duplicates = int(dup.sum())
    acc = acc[~dup]

    # Same MMSI + timestamp at different positions: keep all, flag them.
    conflict = acc.duplicated(subset=["mmsi", "base_datetime"], keep=False)
    acc.loc[conflict, "quality_flags"] = acc.loc[conflict, "quality_flags"] | int(
        QualityFlag.CONFLICTING_TIMESTAMP
    )
    flag_counts[QualityFlag.CONFLICTING_TIMESTAMP.name] = int(conflict.sum())

    reject_counts = {r: int((reason == r).sum()) for r in REJECT_REASONS}
    return ValidationResult(acc, rejected, reject_counts, flag_counts, duplicates)


def describe_flags(value: int) -> list[str]:
    """Human-readable names for a quality_flags bitmask."""
    return [f.name for f in QualityFlag if f is not QualityFlag.NONE and value & f]
