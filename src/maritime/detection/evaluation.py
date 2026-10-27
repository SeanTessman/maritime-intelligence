"""Evaluation.

* `run_synthetic_benchmark`: precision / recall / F1 / FPR / confusion matrix on the
  SYNTHETIC labeled benchmark (the only place ground truth exists).
* `stability_report`: for unlabeled real AIS data, score distributions, seed stability,
  day-to-day shift, and rule-vs-model overlap. No accuracy is claimed for real data.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import average_precision_score, confusion_matrix

from maritime.config import PROJECT_ROOT
from maritime.detection import isolation_forest as iforest
from maritime.detection.rules import run_rules


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true, y_pred = np.asarray(y_true, bool), np.asarray(y_pred, bool)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[False, True]).ravel()
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
    }


def _threshold_sweep(model, train: pd.DataFrame, test: pd.DataFrame, y: np.ndarray) -> list[dict]:  # noqa: ANN001
    """IF metrics when the review threshold is set at different training-score quantiles."""
    train_scores = model.score(train)
    out = []
    for q in (0.80, 0.90, 0.95, 0.99):
        thr = float(np.quantile(train_scores, q))
        m = binary_metrics(y, test["score"].to_numpy() > thr)
        out.append(
            {
                "train_quantile": q,
                "threshold": round(thr, 4),
                **{
                    k: m[k]
                    for k in ("tp", "fp", "fn", "precision", "recall", "f1", "false_positive_rate")
                },
            }
        )
    return out


def run_synthetic_benchmark(seed: int = 42, write: bool = True) -> dict[str, Any]:
    """End-to-end detection on the synthetic benchmark with a time split."""
    from maritime.processing.pipeline import compute_steps, load_thresholds
    from maritime.processing.vessel_features import compute_track_features
    from maritime.synthetic import BenchmarkConfig, benchmark_ports, make_benchmark

    cfg = load_thresholds()
    bcfg = BenchmarkConfig()
    pos, labels = make_benchmark(bcfg)
    ports = benchmark_ports()
    steps, _ = compute_steps(pos, ports, cfg)
    tracks = compute_track_features(
        steps, cfg["trajectory"]["gap_threshold_s"], cfg["proximity"]["radius_m"]
    )
    tracks = tracks.merge(
        labels[["track_id", "is_anomaly", "anomaly_kind"]], on="track_id", how="left"
    )
    tracks["is_anomaly"] = tracks["is_anomaly"].fillna(False).astype(bool)
    train_end = (bcfg.start_date + pd.Timedelta(days=4)).date()
    train, test = iforest.time_split(tracks, train_end)

    model = iforest.train(train, cfg["isolation_forest"], seed=seed, version="synthetic-benchmark")
    test = test.assign(score=model.score(test))
    test["model_flag"] = test["score"] > model.threshold

    test_steps = steps[steps["base_datetime"].dt.date > train_end]
    events = run_rules(test_steps, test, cfg["rules"])
    flagged = set(events["track_id"]) if not events.empty else set()
    test["rule_flag"] = test["track_id"].isin(flagged)
    test["either_flag"] = test["rule_flag"] | test["model_flag"]
    rule_types = (
        events.groupby("track_id")["anomaly_type"].apply(lambda s: sorted(set(s)))
        if not events.empty
        else pd.Series(dtype=object)
    )
    test["rule_types"] = test["track_id"].map(rule_types)

    y = test["is_anomaly"].to_numpy()
    by_kind = {}
    for kind, g in test[test["is_anomaly"]].groupby("anomaly_kind"):
        by_kind[kind] = {
            "n": len(g),
            "rule_recall": round(float(g["rule_flag"].mean()), 3),
            "model_recall": round(float(g["model_flag"].mean()), 3),
        }
    result = {
        "dataset": "SYNTHETIC benchmark (src/maritime/synthetic.py, seed=7) - not NOAA data",
        "unit": "vessel-day track",
        "split": {
            "train_days": f"<= {train_end}",
            "n_train": len(train),
            "n_test": len(test),
            "test_positives": int(y.sum()),
        },
        "rules": binary_metrics(y, test["rule_flag"]),
        "isolation_forest": {
            **binary_metrics(y, test["model_flag"]),
            "average_precision": round(float(average_precision_score(y, test["score"])), 4),
            "threshold": round(model.threshold, 4),
        },
        "isolation_forest_threshold_sweep": _threshold_sweep(model, train, test, y),
        "rules_or_model": binary_metrics(y, test["either_flag"]),
        "recall_by_injected_kind": by_kind,
        "seed": seed,
    }
    if write:
        out = PROJECT_ROOT / "reports" / "model_cards"
        out.mkdir(parents=True, exist_ok=True)
        (out / "synthetic_benchmark_results.json").write_text(json.dumps(result, indent=2))
        test[["track_id", "is_anomaly", "anomaly_kind", "score", "model_flag", "rule_flag"]].to_csv(
            PROJECT_ROOT / "reports" / "model_cards" / "synthetic_benchmark_predictions.csv",
            index=False,
        )
    return result


def stability_report(
    train: pd.DataFrame,
    evals: pd.DataFrame,
    cfg: dict,
    rule_track_ids: set[str],
    seeds: tuple[int, ...] = (0, 1, 2, 3, 4),
    top_k: int = 20,
) -> dict[str, Any]:
    """Unlabeled real-data diagnostics. No ground truth is assumed."""
    scores = {}
    for s in seeds:
        m = iforest.train(train, cfg, seed=s, version=f"stability-{s}")
        scores[s] = m.score(evals)
    mat = np.array([scores[s] for s in seeds])
    rho = [
        stats.spearmanr(mat[i], mat[j]).statistic
        for i in range(len(seeds))
        for j in range(i + 1, len(seeds))
    ]
    tops = [set(np.argsort(-mat[i])[:top_k]) for i in range(len(seeds))]
    jac = [
        len(tops[i] & tops[j]) / len(tops[i] | tops[j])
        for i in range(len(seeds))
        for j in range(i + 1, len(seeds))
    ]
    base = iforest.train(train, cfg, seed=seeds[0], version="stability-base")
    tr_scores, ev_scores = base.score(train), base.score(evals)
    ks = stats.ks_2samp(tr_scores, ev_scores)
    model_flag = ev_scores > base.threshold
    rule_flag = evals["track_id"].isin(rule_track_ids).to_numpy()
    both = int((model_flag & rule_flag).sum())
    return {
        "n_train_tracks": len(train),
        "n_eval_tracks": len(evals),
        "score_quantiles_train": {
            q: round(float(np.quantile(tr_scores, q)), 4) for q in (0.5, 0.9, 0.99)
        },
        "score_quantiles_eval": {
            q: round(float(np.quantile(ev_scores, q)), 4) for q in (0.5, 0.9, 0.99)
        },
        "seed_stability": {
            "seeds": list(seeds),
            "mean_spearman": round(float(np.mean(rho)), 4),
            "min_spearman": round(float(np.min(rho)), 4),
            f"mean_top{top_k}_jaccard": round(float(np.mean(jac)), 4),
        },
        "train_vs_eval_shift": {
            "ks_statistic": round(float(ks.statistic), 4),
            "p_value": float(ks.pvalue),
        },
        "eval_flagged": {
            "model": int(model_flag.sum()),
            "rules": int(rule_flag.sum()),
            "both": both,
            "model_only": int((model_flag & ~rule_flag).sum()),
            "rules_only": int((rule_flag & ~model_flag).sum()),
        },
        "note": "Real AIS data has no ground-truth labels; these are stability and agreement "
        "diagnostics, not accuracy.",
    }
