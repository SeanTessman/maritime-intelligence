"""Detection job on real (and demo-synthetic) data in the database.

1. Load steps/track features produced by `process`.
2. Rule-based detection on every fix and track.
3. Isolation Forest trained on REAL tracks from the training days only, then used to
   score all real tracks (training days are labelled in-sample).
4. Upsert anomaly_events and a model_runs record; write diagnostics for the model card.

Synthetic demo vessels (is_synthetic) are never used to train the real-data model.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from maritime.config import PROJECT_ROOT, get_settings
from maritime.database import get_engine
from maritime.detection import isolation_forest as iforest
from maritime.detection.evaluation import stability_report
from maritime.detection.explanations import explain_model, explain_rule, top_contributors
from maritime.detection.rules import run_rules
from maritime.ingestion.validate_ais import describe_flags
from maritime.processing.pipeline import load_thresholds

logger = logging.getLogger(__name__)


def _rules_run_key(cfg: dict) -> str:
    import hashlib

    return (
        "rules_"
        + hashlib.sha256(json.dumps(cfg["rules"], sort_keys=True).encode()).hexdigest()[:10]
    )


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def upsert_events(
    engine: Engine, events: pd.DataFrame, run_key: str, model_run_id: int | None = None
) -> int:
    if events.empty:
        return 0
    sql = text(
        """
        INSERT INTO anomaly_events (run_key, detector, anomaly_type, mmsi, event_time, end_time,
            lat, lon, score, rule_flags, features, explanation, quality_notes,
            supporting_position_ids, model_run_id)
        VALUES (:run_key, :detector, :anomaly_type, :mmsi, :event_time, :end_time, :lat, :lon,
            :score, CAST(:rule_flags AS jsonb), CAST(:features AS jsonb), :explanation,
            :quality_notes, :supporting_position_ids, :model_run_id)
        ON CONFLICT (run_key, detector, anomaly_type, mmsi, event_time) DO UPDATE SET
            end_time = EXCLUDED.end_time, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
            score = EXCLUDED.score, rule_flags = EXCLUDED.rule_flags,
            features = EXCLUDED.features, explanation = EXCLUDED.explanation,
            quality_notes = EXCLUDED.quality_notes,
            supporting_position_ids = EXCLUDED.supporting_position_ids,
            model_run_id = EXCLUDED.model_run_id
        """
    )
    rows = []
    for r in events.to_dict("records"):
        rows.append(
            {
                "run_key": run_key,
                "detector": r["detector"],
                "anomaly_type": r["anomaly_type"],
                "mmsi": r["mmsi"],
                "event_time": r["event_time"],
                "end_time": r.get("end_time"),
                "lat": None if pd.isna(r.get("lat")) else float(r["lat"]),
                "lon": None if pd.isna(r.get("lon")) else float(r["lon"]),
                "score": None
                if r.get("score") is None or pd.isna(r.get("score"))
                else float(r["score"]),
                "rule_flags": json.dumps(_jsonable(r.get("rule_flags"))),
                "features": json.dumps(_jsonable(r.get("features"))),
                "explanation": r["explanation"],
                "quality_notes": r.get("quality_notes"),
                "supporting_position_ids": [
                    int(x) for x in (r.get("supporting_position_ids") or [])
                ],
                "model_run_id": model_run_id,
            }
        )
    with engine.begin() as conn:
        for i in range(0, len(rows), 1000):
            conn.execute(sql, rows[i : i + 1000])
    return len(rows)


def record_model_run(
    engine: Engine,
    model: iforest.TrackModel,
    artifact: str,
    n_train: int,
    n_eval: int,
    eval_window: tuple[str, str],
    metrics: dict,
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO model_runs (model_name, model_version, params, feature_list,
                    train_start, train_end, eval_start, eval_end, n_train, n_eval, data_hash,
                    artifact_path, library_versions, metrics, is_synthetic)
                VALUES (:name, :ver, CAST(:params AS jsonb), CAST(:feats AS jsonb), :ts, :te,
                    :es, :ee, :ntr, :nev, :dh, :art, CAST(:lib AS jsonb), CAST(:met AS jsonb), false)
                ON CONFLICT (model_name, model_version) DO UPDATE SET
                    metrics = EXCLUDED.metrics, artifact_path = EXCLUDED.artifact_path,
                    created_at = now()
                RETURNING model_run_id
                """
            ),
            {
                "name": iforest.MODEL_NAME,
                "ver": model.version,
                "params": json.dumps({**model.params, "threshold": model.threshold}),
                "feats": json.dumps(list(model.features)),
                "ts": model.train_window[0],
                "te": model.train_window[1],
                "es": eval_window[0],
                "ee": eval_window[1],
                "ntr": n_train,
                "nev": n_eval,
                "dh": model.data_hash,
                "art": artifact,
                "lib": json.dumps(model.library_versions),
                "met": json.dumps(_jsonable(metrics)),
            },
        ).scalar_one()


def _model_events(
    tracks: pd.DataFrame, steps: pd.DataFrame, model: iforest.TrackModel
) -> pd.DataFrame:
    flagged = tracks[tracks["score"] > model.threshold]
    rows = []
    for t in flagged.itertuples():
        pts = steps[
            (steps["mmsi"] == t.mmsi)
            & (steps["base_datetime"] >= t.start_time)
            & (steps["base_datetime"] <= t.end_time)
        ]
        sample = pts.iloc[np.linspace(0, len(pts) - 1, min(len(pts), 50)).astype(int)]
        contrib = top_contributors(pd.Series(t._asdict()), model.train_stats)
        flags = sorted({f for q in pts["quality_flags"].unique() for f in describe_flags(int(q))})
        notes = [f"split: {t.split}"]
        if flags:
            notes.append("data-quality flags present in track: " + ", ".join(flags))
        rows.append(
            {
                "detector": "isolation_forest",
                "anomaly_type": "model_outlier_track",
                "mmsi": t.mmsi,
                "event_time": t.start_time,
                "end_time": t.end_time,
                "lat": float(pts["lat"].median()),
                "lon": float(pts["lon"].median()),
                "score": float(t.score),
                "rule_flags": None,
                "features": {
                    "track_id": t.track_id,
                    "top_contributors": contrib,
                    "vessel_type_group": t.vessel_type_group,
                    "split": t.split,
                },
                "explanation": explain_model(float(t.score), model.threshold, contrib),
                "quality_notes": "; ".join(notes),
                "supporting_position_ids": sample["position_id"].astype(int).tolist(),
            }
        )
    return pd.DataFrame(rows)


def run_detection(engine: Engine | None = None, seed: int = 42) -> dict[str, Any]:
    engine = engine or get_engine()
    settings = get_settings()
    cfg = load_thresholds()
    steps = pd.read_parquet(settings.processed_dir / "steps.parquet")
    tracks = pd.read_parquet(settings.processed_dir / "track_features.parquet")
    min_obs = cfg["isolation_forest"]["min_track_obs"]

    # --- rules (real and demo-synthetic, separate run keys) ---------------------
    syn_mmsi = set(tracks.loc[tracks["is_synthetic"], "mmsi"])
    summary: dict[str, Any] = {}
    rule_key = _rules_run_key(cfg)
    rule_track_ids: set[str] = set()
    for label, mask_s, mask_t in (
        ("real", ~steps["mmsi"].isin(syn_mmsi), ~tracks["is_synthetic"]),
        ("synthetic_demo", steps["mmsi"].isin(syn_mmsi), tracks["is_synthetic"]),
    ):
        st, tr = steps[mask_s], tracks[mask_t & (tracks["n_obs"] >= min_obs)]
        if st.empty:
            continue
        ev = run_rules(st, tr, cfg["rules"])
        if ev.empty:
            continue
        ev["explanation"] = [explain_rule(e) for e in ev.to_dict("records")]
        key = rule_key if label == "real" else rule_key + "_synthetic"
        summary[f"rule_events_{label}"] = upsert_events(engine, ev, key)
        summary[f"rule_events_by_type_{label}"] = ev["anomaly_type"].value_counts().to_dict()
        if label == "real":
            rule_track_ids = set(ev["track_id"])

    # --- Isolation Forest on real tracks ------------------------------------
    real = tracks[~tracks["is_synthetic"] & (tracks["n_obs"] >= min_obs)].copy()
    train, evals = iforest.time_split(real, settings.miadp_train_end)
    model = iforest.train(
        train,
        cfg["isolation_forest"],
        seed=seed,
        version=f"s{seed}_{iforest.data_hash(iforest.validate_feature_frame(train))}",
    )
    real["score"] = model.score(real)
    real["split"] = np.where(
        pd.to_datetime(real["track_date"]).dt.date <= settings.miadp_train_end,
        "train (in-sample)",
        "evaluation (out-of-sample)",
    )
    artifact = settings.miadp_models_dir / f"{iforest.MODEL_NAME}_{model.version}.joblib"
    model.save(artifact)

    diag = stability_report(train, evals, cfg["isolation_forest"], rule_track_ids)
    eval_window = (str(evals["start_time"].min()), str(evals["end_time"].max()))
    run_id = record_model_run(
        engine,
        model,
        str(artifact.relative_to(PROJECT_ROOT)),
        len(train),
        len(evals),
        eval_window,
        diag,
    )
    mev = _model_events(real, steps, model)
    summary["model_events"] = upsert_events(
        engine, mev, f"{iforest.MODEL_NAME}_{model.version}", run_id
    )

    out = settings.processed_dir
    real[["track_id", "mmsi", "track_date", "vessel_type_group", "score", "split"]].to_parquet(
        out / "track_scores.parquet", index=False
    )
    cards = PROJECT_ROOT / "reports" / "model_cards"
    cards.mkdir(parents=True, exist_ok=True)
    (cards / "real_data_diagnostics.json").write_text(
        json.dumps(
            {
                "model_version": model.version,
                "threshold": model.threshold,
                "seed": seed,
                "train_window": model.train_window,
                "eval_window": eval_window,
                **diag,
            },
            indent=2,
            default=str,
        )
    )
    return {
        "model_version": model.version,
        "model_run_id": run_id,
        "threshold": model.threshold,
        "n_train": len(train),
        "n_eval": len(evals),
        **summary,
        "diagnostics": diag,
    }
