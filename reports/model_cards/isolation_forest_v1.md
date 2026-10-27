# Model card: `isolation_forest_track` (version `s42_d786048cb7908ff8`)

> Educational portfolio model. Scores represent statistical unusualness relative to one
> week of harbour traffic, not evidence of illegal or hostile activity.

## Model details

| | |
|---|---|
| Type | scikit-learn `IsolationForest` inside a `Pipeline` (median `SimpleImputer` -> `RobustScaler` -> forest) |
| Hyper-parameters | `n_estimators=300`, `max_samples="auto"`, `contamination="auto"`, `random_state=42` |
| Unit scored | vessel-day track (MMSI x UTC date), ≥ 10 fixes |
| Inputs | 22 track features (`MODEL_FEATURES` in `src/maritime/processing/vessel_features.py`) |
| Output | `anomaly_score = -score_samples(X)`; higher = more easily isolated = more unusual |
| Review threshold | 0.5872 = 99th percentile of training scores (a ~1% review budget) |
| Artifact | `models/isolation_forest_track_s42_d786048cb7908ff8.joblib` (+ `.json` metadata); `model_runs` row |
| Libraries | scikit-learn 1.9.1, numpy, pandas (exact versions in the JSON metadata) |
| Version scheme | `s<seed>_<sha256 of training feature matrix, 16 hex>`; same data + seed -> same version |

## Intended use

Ranking vessel-day tracks in the study area for **human review**, alongside the rule
engine, inside an educational dashboard. Each flagged track ships with its most unusual
features and data-quality flags so a reviewer can find the ordinary explanation quickly.

## Out-of-scope and inappropriate uses

- Deciding that any vessel, owner, flag or organisation did something wrong.
- Treating the score as a probability, a risk rating, or a threat level.
- Treating `contamination`, the 1% review budget, or the number of flagged tracks as a
  base rate of real-world events.
- Applying the model to other regions, seasons or data sources without retraining and
  re-validation.
- Any operational, enforcement, safety-of-navigation or targeting purpose.

## Training and evaluation data

- NOAA Marine Cadastre AIS, NY Harbor bbox (40.3-41.1 N, 74.4-73.4 W).
- **Train:** Jan 9-13 2023, 1,937 tracks. **Evaluation:** Jan 14-15 2023, 655 tracks. The
  split is by date and asserted disjoint; the evaluation days include a weekend.
- Synthetic demo vessels are excluded from training and scoring. Density features for
  real and synthetic data are computed separately so synthetic rows cannot change real
  features (verified: adding the synthetic slice leaves the model version hash unchanged).

## Features

Movement (mean/max/std/p95 SOG, mean speed change, max acceleration, mean/p95 course
change, max implied speed, p95 implied-vs-reported discrepancy), trajectory (route
length, efficiency, heading variation, low-speed fraction, longest low-speed run),
geospatial (min/mean distance to WPI ports, fraction near port, traffic density) and
reporting (longest gap, hourly coverage continuity, impossible jumps). Raw counts
(`n_obs`, `duration_h`, `obs_per_hour`, `n_gaps`) are excluded so the model does not
simply rank prolific transmitters.

## Evaluation

### Synthetic benchmark (the only labelled data)

Same pipeline on 200 synthetic vessels x 7 days with 10% injected anomalies; train days
1-5, test days 6-7 (290 tracks, 31 positives).

| threshold (train quantile) | precision | recall | F1 | FPR |
|---|---|---|---|---|
| 0.80 | 0.462 | 0.968 | 0.625 | 0.135 |
| 0.90 | 0.667 | 0.903 | 0.767 | 0.054 |
| 0.95 | 0.900 | 0.581 | 0.706 | 0.008 |
| **0.99 (deployed)** | 1.000 | 0.129 | 0.229 | 0.000 |

Average precision 0.872. Per injected kind at the deployed threshold, recall is highest for
3-hour reporting gaps (0.6) and lowest for single-fix position jumps and short course
reversals (0.0): these change one or two fixes in a day and barely move day-level
features; the rule engine is the right tool for them (rule recall 0.97 overall).

**Interpretation.** The ranking is informative; the deployed 99th-percentile threshold is
conservative because training days themselves contain anomalies. The benchmark verifies
behaviour on anomalies the author designed; it is not an estimate of real-world accuracy.

### Real data (unlabelled; no accuracy claimed)

| diagnostic | value |
|---|---|
| Score median / p90 / p99 (train) | 0.424 / 0.531 / 0.594 |
| Score median / p90 / p99 (evaluation) | 0.419 / 0.527 / 0.601 |
| Seed stability (5 seeds, eval tracks) | mean Spearman 0.992, min 0.988; mean top-20 Jaccard 0.727 |
| Train vs eval shift | KS 0.058, p = 0.073 |
| Eval tracks flagged | model 10, rules 121, both 7 |

Qualitative review of the top 15 (by feature pattern only): fast small craft with generic
AIS type codes, vessel-days with very long gaps and brief presence, and transits through
the outer approaches far from WPI points. Each has an ordinary explanation.

## Limitations and known failure modes

- Vessel-type mix drives scores: fast ferries and small fast craft rank high.
- One week of data; weekday/weekend and seasonal effects are only weakly represented.
- Port-distance features inherit WPI's coarse single-point geometry.
- In-sample scores (training days) are optimistic; the dashboard labels each flagged track
  `train (in-sample)` or `evaluation (out-of-sample)`.
- Feature importance shown to users is a robust-deviation heuristic, not a model-exact
  attribution (Isolation Forest has no native per-sample attribution).

## Reproduce

```powershell
python tasks.py process
python tasks.py detect      # trains, scores, writes model_runs + reports/model_cards/real_data_diagnostics.json
python tasks.py evaluate    # synthetic benchmark -> reports/model_cards/synthetic_benchmark_results.json
```
