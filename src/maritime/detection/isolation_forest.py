"""Isolation Forest on vessel-day track features.

Score convention: `anomaly_score = -score_samples(X)`, so HIGHER means MORE unusual
relative to the training tracks. The score is a rank-able isolation measure; it is not
a probability, and it says nothing about intent.

The review threshold is the `alert_quantile` (default 0.99) of TRAINING scores, i.e. a
review budget of ~1% of normal-period tracks. `contamination` stays 'auto' and is not
interpreted as a base rate of anything.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from maritime.processing.vessel_features import MODEL_FEATURES

MODEL_NAME = "isolation_forest_track"


def validate_feature_frame(
    df: pd.DataFrame, features: tuple[str, ...] = MODEL_FEATURES
) -> pd.DataFrame:
    """Return df[features] as float, raising if any expected column is missing."""
    missing = [f for f in features if f not in df.columns]
    if missing:
        raise ValueError(f"feature frame missing columns: {missing}")
    X = df[list(features)].astype(float)
    return X.replace([np.inf, -np.inf], np.nan)


def data_hash(df: pd.DataFrame) -> str:
    h = hashlib.sha256(pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values)
    return h.hexdigest()[:16]


@dataclass
class TrackModel:
    pipeline: Pipeline
    features: tuple[str, ...]
    seed: int
    params: dict[str, Any]
    threshold: float
    train_stats: dict[str, dict[str, float]]
    version: str
    trained_at: str
    train_window: tuple[str, str] | None = None
    data_hash: str | None = None
    library_versions: dict[str, str] = field(default_factory=dict)

    def score(self, df: pd.DataFrame) -> np.ndarray:
        X = validate_feature_frame(df, self.features)
        return -self.pipeline.score_samples(X)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        meta = {k: v for k, v in self.__dict__.items() if k != "pipeline"}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
        return path

    @staticmethod
    def load(path: Path) -> TrackModel:
        obj = joblib.load(path)  # noqa: S301 - project-generated artifact only
        if not isinstance(obj, TrackModel):
            raise TypeError(f"{path} is not a TrackModel")
        return obj


def train(
    train_df: pd.DataFrame,
    cfg: dict,
    seed: int = 42,
    features: tuple[str, ...] = MODEL_FEATURES,
    version: str | None = None,
) -> TrackModel:
    X = validate_feature_frame(train_df, features)
    params = {
        "n_estimators": int(cfg.get("n_estimators", 300)),
        "max_samples": cfg.get("max_samples", "auto"),
        "contamination": cfg.get("contamination", "auto"),
        "random_state": seed,
    }
    pipe = Pipeline(
        [
            # All-NaN columns are kept (filled with 0) so the feature schema never changes.
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", RobustScaler()),
            ("iforest", IsolationForest(**params, n_jobs=1)),
        ]
    )
    pipe.fit(X)
    train_scores = -pipe.score_samples(X)
    stats = {
        f: {
            "median": float(np.nanmedian(X[f])) if X[f].notna().any() else 0.0,
            "iqr": float(np.nanpercentile(X[f], 75) - np.nanpercentile(X[f], 25))
            if X[f].notna().any()
            else 0.0,
        }
        for f in features
    }
    tw = None
    if "start_time" in train_df:
        tw = (str(train_df["start_time"].min()), str(train_df["end_time"].max()))
    return TrackModel(
        pipeline=pipe,
        features=tuple(features),
        seed=seed,
        params=params,
        threshold=float(np.quantile(train_scores, cfg.get("alert_quantile", 0.99))),
        train_stats=stats,
        version=version or datetime.now(UTC).strftime("v%Y%m%dT%H%M%S"),
        trained_at=datetime.now(UTC).isoformat(),
        train_window=tw,
        data_hash=data_hash(X),
        library_versions={
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    )


def time_split(tracks: pd.DataFrame, train_end) -> tuple[pd.DataFrame, pd.DataFrame]:  # noqa: ANN001
    """Split by track_date: <= train_end is training, later days are evaluation."""
    d = pd.to_datetime(tracks["track_date"]).dt.date
    tr, ev = tracks[d <= train_end], tracks[d > train_end]
    if len(tr) and len(ev):
        assert pd.to_datetime(tr["track_date"]).max() < pd.to_datetime(ev["track_date"]).min()
    return tr.copy(), ev.copy()
