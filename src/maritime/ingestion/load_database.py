"""Load validated AIS chunks into PostgreSQL with COPY, idempotently and resumably.

Per chunk, inside ONE transaction:
  1. COPY the accepted rows into a temp staging table.
  2. Upsert vessel static data (latest non-null value wins, first/last seen widened).
  3. INSERT ... SELECT ... ON CONFLICT DO NOTHING into ais_positions.
  4. Advance ingestion_runs.last_chunk_done and save the running quality report.

Because the checkpoint is committed atomically with the data, a crash can only lose
the in-flight chunk, which is redone on resume. Re-running a completed file is a no-op.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Engine, text

from maritime.config import BoundingBox, get_settings
from maritime.database import get_engine
from maritime.ingestion.parse_ais import bbox_mask, iter_raw_chunks, normalize_chunk
from maritime.ingestion.quality import QualityAccumulator
from maritime.ingestion.validate_ais import validate_chunk

logger = logging.getLogger(__name__)

POSITION_COLS = [
    "mmsi",
    "base_datetime",
    "lat",
    "lon",
    "sog",
    "cog",
    "heading",
    "nav_status",
    "draft_m",
    "quality_flags",
]
VESSEL_COLS = [
    "mmsi",
    "vessel_name",
    "imo",
    "call_sign",
    "vessel_type",
    "length_m",
    "width_m",
    "draft_m",
    "cargo",
    "transceiver_class",
    "first_seen",
    "last_seen",
]


@dataclass(frozen=True)
class SourceInfo:
    name: str
    publisher: str
    url: str | None = None
    license: str | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    is_synthetic: bool = False
    notes: str | None = None
    access_date: date | None = None


def file_sha256(path: Path, bufsize: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def ensure_source(conn: Connection, src: SourceInfo) -> int:
    """Insert or update a data_sources row by name and return its id."""
    return conn.execute(
        text(
            """
            INSERT INTO data_sources (name, publisher, url, access_date, coverage_start,
                                      coverage_end, license, is_synthetic, notes)
            VALUES (:name, :publisher, :url, COALESCE(:access_date, CURRENT_DATE),
                    :coverage_start, :coverage_end, :license, :is_synthetic, :notes)
            ON CONFLICT (name) DO UPDATE SET
                coverage_start = LEAST(data_sources.coverage_start, EXCLUDED.coverage_start),
                coverage_end = GREATEST(data_sources.coverage_end, EXCLUDED.coverage_end),
                url = EXCLUDED.url, license = EXCLUDED.license, notes = EXCLUDED.notes
            RETURNING source_id
            """
        ),
        src.__dict__,
    ).scalar_one()


def _copy_df(conn: Connection, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    """COPY a DataFrame into `table` using CSV text (fast path through psycopg 3)."""
    buf = io.StringIO()
    df[columns].to_csv(buf, index=False, header=False, na_rep="\\N")
    raw = conn.connection.dbapi_connection
    cols = ", ".join(columns)  # column names are internal constants, never user input
    with (
        raw.cursor() as cur,
        cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as copy,
    ):
        copy.write(buf.getvalue())


def _vessel_rows(acc: pd.DataFrame) -> pd.DataFrame:
    """Latest non-null static fields per MMSI within the chunk."""
    acc = acc.sort_values("base_datetime")
    static = [
        "vessel_name",
        "imo",
        "call_sign",
        "vessel_type",
        "length_m",
        "width_m",
        "draft_m",
        "cargo",
        "transceiver_class",
    ]
    g = acc.groupby("mmsi", sort=False)
    out = g[static].last()  # GroupBy.last skips nulls -> latest non-null per column
    out["first_seen"] = g["base_datetime"].min()
    out["last_seen"] = g["base_datetime"].max()
    return out.reset_index()


def load_chunk(
    conn: Connection,
    acc: pd.DataFrame,
    source_id: int,
    run_id: int | None,
    is_synthetic: bool = False,
) -> int:
    """Stage and insert one validated chunk. Returns the number of new position rows."""
    if acc.empty:
        return 0
    conn.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS stage_pos (mmsi text, base_datetime timestamptz, "
            "lat float8, lon float8, sog real, cog real, heading real, nav_status int, "
            "draft_m real, quality_flags int) ON COMMIT DELETE ROWS"
        )
    )
    conn.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS stage_vessels (mmsi text, vessel_name text, imo text, "
            "call_sign text, vessel_type int, length_m real, width_m real, draft_m real, cargo int, "
            "transceiver_class text, first_seen timestamptz, last_seen timestamptz) "
            "ON COMMIT DELETE ROWS"
        )
    )
    _copy_df(conn, "stage_vessels", _vessel_rows(acc), VESSEL_COLS)
    conn.execute(
        text(
            """
            INSERT INTO vessels AS v (mmsi, vessel_name, imo, call_sign, vessel_type, length_m,
                width_m, draft_m, cargo, transceiver_class, first_seen, last_seen, source_id,
                is_synthetic)
            SELECT mmsi, vessel_name, imo, call_sign, vessel_type, length_m, width_m, draft_m,
                   cargo, transceiver_class, first_seen, last_seen, :source_id, :syn
            FROM stage_vessels
            ON CONFLICT (mmsi) DO UPDATE SET
                vessel_name = COALESCE(EXCLUDED.vessel_name, v.vessel_name),
                imo = COALESCE(EXCLUDED.imo, v.imo),
                call_sign = COALESCE(EXCLUDED.call_sign, v.call_sign),
                vessel_type = COALESCE(EXCLUDED.vessel_type, v.vessel_type),
                length_m = COALESCE(EXCLUDED.length_m, v.length_m),
                width_m = COALESCE(EXCLUDED.width_m, v.width_m),
                draft_m = COALESCE(EXCLUDED.draft_m, v.draft_m),
                cargo = COALESCE(EXCLUDED.cargo, v.cargo),
                transceiver_class = COALESCE(EXCLUDED.transceiver_class, v.transceiver_class),
                first_seen = LEAST(v.first_seen, EXCLUDED.first_seen),
                last_seen = GREATEST(v.last_seen, EXCLUDED.last_seen),
                updated_at = now()
            """
        ),
        {"source_id": source_id, "syn": is_synthetic},
    )
    pos = acc.copy()
    pos["base_datetime"] = pos["base_datetime"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    _copy_df(conn, "stage_pos", pos, POSITION_COLS)
    result = conn.execute(
        text(
            """
            INSERT INTO ais_positions (mmsi, base_datetime, lat, lon, sog, cog, heading,
                nav_status, draft_m, quality_flags, source_id, run_id)
            SELECT mmsi, base_datetime, lat, lon, sog, cog, heading, nav_status, draft_m,
                   quality_flags, :source_id, :run_id
            FROM stage_pos
            ON CONFLICT ON CONSTRAINT uq_ais_positions_obs DO NOTHING
            """
        ),
        {"source_id": source_id, "run_id": run_id},
    )
    return int(result.rowcount or 0)


def _get_or_start_run(
    conn: Connection, source_id: int, path: Path, sha: str, bbox: BoundingBox
) -> dict[str, Any]:
    row = (
        conn.execute(
            text("SELECT * FROM ingestion_runs WHERE file_sha256 = :sha AND bbox_key = :bk"),
            {"sha": sha, "bk": bbox.key},
        )
        .mappings()
        .first()
    )
    if row is None:
        row = (
            conn.execute(
                text(
                    "INSERT INTO ingestion_runs (source_id, file_name, file_sha256, bbox_key, status) "
                    "VALUES (:sid, :fn, :sha, :bk, 'running') RETURNING *"
                ),
                {"sid": source_id, "fn": path.name, "sha": sha, "bk": bbox.key},
            )
            .mappings()
            .one()
        )
    elif row["status"] != "completed":
        conn.execute(
            text("UPDATE ingestion_runs SET status='running', error_message=NULL WHERE run_id=:r"),
            {"r": row["run_id"]},
        )
    return dict(row)


def ingest_file(
    path: Path,
    source: SourceInfo,
    engine: Engine | None = None,
    bbox: BoundingBox | None = None,
    block_size: int = 32 << 20,
    rejected_sample_dir: Path | None = None,
    fail_after_chunk: int | None = None,
) -> dict[str, Any]:
    """Ingest one AIS file. Returns a summary dict (also persisted in ingestion_runs).

    `fail_after_chunk` exists only so tests can simulate a crash mid-file.
    """
    engine = engine or get_engine()
    bbox = bbox or get_settings().bbox
    sha = file_sha256(path)
    with engine.begin() as conn:
        source_id = ensure_source(conn, source)
        run = _get_or_start_run(conn, source_id, path, sha, bbox)
    run_id = run["run_id"]
    if run["status"] == "completed":
        logger.info(
            "%s already ingested for bbox %s (run %s); skipping", path.name, bbox.key, run_id
        )
        return {"run_id": run_id, "status": "skipped_already_completed", "file": path.name}

    acc_q = QualityAccumulator.from_dict(run.get("quality_report"))
    resume_from = run["last_chunk_done"] + 1
    if resume_from > 0:
        logger.info("Resuming %s at chunk %d", path.name, resume_from)
    rejected_samples: list[pd.DataFrame] = []
    try:
        for i, raw in enumerate(iter_raw_chunks(path, block_size=block_size)):
            if i < resume_from:
                continue
            norm = normalize_chunk(raw)
            invalid = (
                norm["lat"].isna()
                | norm["lon"].isna()
                | ~(norm["lat"].between(-90, 90) & norm["lon"].between(-180, 180))
            )
            inside = bbox_mask(norm, bbox.as_tuple())
            sub = norm[inside]
            acc_q.add_raw(len(norm), int(invalid.sum()), int((~inside).sum()), len(sub))
            acc_q.add_missingness(sub)
            result = validate_chunk(sub)
            if len(result.rejected) and sum(len(r) for r in rejected_samples) < 1000:
                rejected_samples.append(result.rejected.head(200))
            with engine.begin() as conn:
                inserted = load_chunk(conn, result.accepted, source_id, run_id, source.is_synthetic)
                acc_q.add_validation(result, inserted)
                conn.execute(
                    text(
                        """UPDATE ingestion_runs SET last_chunk_done=:i, rows_raw=:raw,
                           rows_in_bbox=:inb, rows_accepted=:acc, rows_rejected=:rej,
                           rows_duplicate=:dup, rows_inserted=:ins,
                           quality_report=CAST(:qr AS jsonb) WHERE run_id=:r"""
                    ),
                    {
                        "i": i,
                        "raw": acc_q.rows_raw,
                        "inb": acc_q.rows_in_bbox,
                        "acc": acc_q.rows_accepted,
                        "rej": acc_q.rows_rejected,
                        "dup": acc_q.duplicates_in_file + acc_q.duplicates_vs_database,
                        "ins": acc_q.rows_inserted,
                        "qr": json.dumps(acc_q.report(), default=str),
                        "r": run_id,
                    },
                )
            logger.info(
                "%s chunk %d: raw=%d in_bbox=%d accepted=%d inserted=%d",
                path.name,
                i,
                len(norm),
                len(sub),
                len(result.accepted),
                inserted,
            )
            if fail_after_chunk is not None and i >= fail_after_chunk:
                raise RuntimeError(f"Simulated failure after chunk {i}")
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE ingestion_runs SET status='failed', error_message=:e WHERE run_id=:r"),
                {"e": str(exc)[:2000], "r": run_id},
            )
        logger.exception("Ingestion of %s failed; rerun to resume", path.name)
        raise

    report = acc_q.report()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ingestion_runs SET status='completed', finished_at=now(), "
                "quality_report=CAST(:qr AS jsonb) WHERE run_id=:r"
            ),
            {"qr": json.dumps(report, default=str), "r": run_id},
        )
    if rejected_sample_dir is not None and rejected_samples:
        rejected_sample_dir.mkdir(parents=True, exist_ok=True)
        pd.concat(rejected_samples).head(1000).to_csv(
            rejected_sample_dir / f"rejected_sample_{path.stem}.csv", index=False
        )
    summary = {k: v for k, v in report.items() if k != "_state"}
    return {"run_id": run_id, "status": "completed", "file": path.name, **summary}
