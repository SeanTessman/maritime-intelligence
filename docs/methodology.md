# Methodology

> Anomaly detections represent statistical or rule-based deviations, not evidence of
> illegal or hostile activity.

All thresholds live in `config/thresholds.yaml`. They are starting heuristics for a busy
harbour with 1-minute NOAA sampling, not maritime safety, regulatory or intelligence
standards.

## 1. Ingestion and validation

1. **Stream, don't load.** Each daily zip (~900 MB of CSV, ~8 M rows) is read with
   pyarrow's streaming CSV reader in 32 MB blocks (~310k rows each), every column as text.
2. **Type safely.** MMSI/IMO stay text (leading zeros survive); timestamps parse to UTC;
   unparseable values become NULL/NaT and are counted, never silently dropped.
3. **Region filter first.** Rows outside the configured bbox are counted and skipped
   before the more expensive validation (≈97% of nationwide rows).
4. **Hard rejects** (row unusable): missing or malformed MMSI, bad timestamp, missing or
   impossible coordinates, (0, 0). Each gets a reason code; a sample is saved to
   `data/interim/rejected_sample_*.csv`.
5. **Soft flags** (row usable, field not): AIS sentinels (SOG 102.3, COG 360, heading 511)
   and out-of-range values become NULL with a bit set in `quality_flags`, so downstream
   code can tell "unknown" from "zero".
6. **Load.** Per chunk, in one transaction: COPY to a temp table, upsert vessels, `INSERT
   ... ON CONFLICT DO NOTHING` into `ais_positions`, advance the run checkpoint. A crash
   loses at most the in-flight chunk; rerunning a completed file is a no-op (matched on
   SHA-256 + bbox).

## 2. Trajectory reconstruction

- Sort by MMSI then time; drop exact duplicates; for **conflicting fixes** (same MMSI and
  timestamp, different position) keep the one closest to the previous kept fix.
- **Step metrics** per fix: `dt_s`, geodesic `step_m` (haversine, R = 6,371,008.8 m, checked
  against the WGS84 geodesic to 0.5%), `implied_kn = step_m / dt_s`, bearing,
  `|ΔCOG|` (wrapped to 0-180), SOG change, acceleration. Reported SOG and implied speed are
  separate columns and are never mixed.
- **Segmentation:** a new segment starts after a gap > 30 min or a jump with implied speed
  > 100 kn over > 5 km. Maps draw each segment separately, so no line crosses a gap.
- **Gap context** (no intent is inferred):

| context | rule | typical meaning |
|---|---|---|
| `near_study_area_edge` | either end within 3 km of the bbox edge | vessel left/entered the region |
| `in_port` | both ends within 3 km of a WPI port | berthing, coverage shadow, Class B unit off |
| `mid_region_unexplained` | neither | receiver coverage, feed gaps, transmitter off... |

  Absence before a vessel's first fix (or after its last) in the study window is never
  counted as a gap: data-collection boundaries are not observation gaps. In the track
  features, the first fix of each UTC day is likewise treated as a track start.
- **Identity collision indicator:** an MMSI with ≥ 3 impossible jumps is flagged as a
  possible shared/misconfigured MMSI (data-quality signal).

On the real week: 1,844 segments, 1,245 gaps > 30 min (733 in port, 381 mid-region,
131 near the edge), 3 impossible jumps, 1 possible identity collision.

## 3. Port proximity

For every fix, the nearest WPI port within 2.5 km is found in SQL
(`ST_DWithin` + `LATERAL ... ORDER BY geog <-> port LIMIT 1`, GIST-indexed). Consecutive
near-port fixes of the same vessel and port merge into one event unless separated by
> 1 h (a gaps-and-islands window query). 2.5 km is chosen because WPI coordinates are
rounded to ~1.85 km. The same logic exists in pandas (`spatial_features.proximity_events`)
for synthetic data and tests. **Proximity is not docking.**

## 4. Features (unit = vessel-day track)

28 features per MMSI x UTC day (`processing/vessel_features.py`): movement (mean/median/
max/std/p95 SOG, speed change, acceleration, course change, max implied speed, p95
implied-vs-reported discrepancy), trajectory (n fixes, duration, route length,
displacement, efficiency, circular heading variation, low-speed fraction, longest
low-speed run), geospatial (nearest-port distance, fraction near port, traffic density
from a ~1 km grid), reporting (fixes/hour, gaps, longest gap, hourly coverage continuity,
jumps). Vessel-day is the unit because it gives a natural time split, keeps one
long-lived vessel from dominating, and matches the daily file boundary.

## 5. Rule-based detectors

| rule | fires when | default | quality guards |
|---|---|---|---|
| `high_reported_speed` | SOG above vessel-type threshold for ≥ 2 consecutive fixes | cargo 28, tanker 22, passenger 42, tug 18, fishing 20, pleasure 45, other 40 kn | SOG-unavailable excluded; notes 102.2 kn ceiling values and unsupported implied speed |
| `speed_discrepancy` | \|implied − reported\| > 15 kn and ratio > 2 | dt 30-600 s, step > 200 m | jumps excluded; receiver-timestamp caveat |
| `reporting_gap` | gap > 2 h, `mid_region_unexplained`, vessel moving (≥ 1 kn) at either end | | explicit "not evidence of deliberate deactivation" |
| `abrupt_course_change` | \|ΔCOG\| > 120° within 120 s, both fixes ≥ 8 kn | | COG-unavailable excluded |
| `position_jump` | implied > 80 kn over > 2 km | | treated as data quality first |
| `extended_loitering` | ≤ 1 kn for ≥ 3 h, > 3 km from any WPI port | | skipped when self-reported at anchor/moored |
| `peer_deviation` | robust z (median/MAD) > 6 vs same vessel-type group on max SOG, mean SOG or route length | group ≥ 10 tracks | one event per track |

Thresholds were set from domain reasoning, then sanity-checked against the real week's
distributions (e.g. NY Harbor fast ferries routinely exceed 25 kn, so the passenger
threshold is 42 kn). They were not tuned on the synthetic benchmark's labels.

## 6. Isolation Forest

- **Data:** real vessel-day tracks with ≥ 10 fixes. **Time split:** train on Jan 9-13
  (1,937 tracks), evaluate on Jan 14-15 (655 tracks); the split is asserted to be disjoint
  in time.
- **Features:** 22 of the 28 (`MODEL_FEATURES`); pure counts like `n_obs` are excluded so
  the model does not just rank busy transmitters.
- **Pipeline:** median imputation (all-empty columns kept so the schema never changes) ->
  RobustScaler -> `IsolationForest(n_estimators=300, contamination="auto", random_state=42)`.
- **Score:** `-score_samples`, higher = more unusual. **Review threshold:** 99th percentile
  of training scores (0.587), i.e. a review budget of ~1% of normal-period tracks.
  `contamination` is not used as a threshold and is not a base rate of anything.
- **Versioning:** version = seed + hash of the training feature matrix
  (`s42_d786048cb7908ff8`); artifact + JSON metadata in `models/`, a `model_runs` row with
  params, features, windows, library versions and diagnostics.
- **Explanations:** top features by distance from the training median in IQR units, plus
  plain-language caveats.

## 7. Evaluation

**Synthetic benchmark (labels exist only here).** 200 synthetic vessels x 7 days, routes
along approximate harbour channels with smoothed turns, 10% of vessel-days given one
injected anomaly (speed spike, position jump, 3 h gap, 4 h loiter, course reversals).
Train on days 1-5, test on days 6-7 (290 tracks, 31 positives):

| detector | precision | recall | F1 | FPR |
|---|---|---|---|---|
| rules (any) | 1.000 | 0.968 | 0.984 | 0.000 |
| Isolation Forest @ 99th pct | 1.000 | 0.129 | 0.229 | 0.000 |
| Isolation Forest @ 90th pct | 0.667 | 0.903 | 0.767 | 0.054 |
| rules OR model | 1.000 | 1.000 | 1.000 | 0.000 |

Isolation Forest average precision = 0.872. Read these carefully: the injected anomalies
were designed by the same author as the rules, so near-perfect rule scores show the rules
**behave as specified**, not that they would perform like this on real data. The useful
finding is about the model: it **ranks** anomalies well (AP 0.87) but the 99th-percentile
threshold recovers few of them because the training days themselves contain ~10%
anomalies. Threshold choice, not the model, dominates recall.

**Real data (no labels; no accuracy claimed).**

- Seed stability (5 seeds): mean Spearman 0.992 (min 0.988) on evaluation scores; mean
  top-20 Jaccard 0.73.
- Train vs evaluation score shift: KS = 0.058, p = 0.073 (no strong evidence of drift
  between weekdays and the weekend).
- Agreement on the 655 evaluation tracks: model flagged 10, rules flagged 121, both 7.
- Qualitative review of the 15 top-ranked tracks (aggregate, by feature pattern):
  (a) fast small craft, mostly typed "other" or "pleasure/sailing" (mean SOG 15-28 kn,
  frequent speed changes), consistent with fast ferries, pilot boats or patrol craft whose
  AIS type code is generic; (b) vessel-days with very long gaps (11-19 h) and only brief
  presence in the box; (c) transits through the outer approaches 37-48 km from any WPI
  point in low-density water. All three have ordinary explanations (vessel role, region
  boundary, port geography), which is what an unsupervised ranking on one week should be
  expected to surface first.

## 8. Network analysis

Heterogeneous NetworkX graph of the real week: 596 vessels, 41 ports, 61 grid regions,
1,111 port-touching voyages; 41,551 edges. Edge types: `NEAR_PORT`, `IN_REGION`,
`PART_OF_VOYAGE`, `VOYAGE_ENDPOINT`, `SHARED_PORT_PROXIMITY` (≥ 15 min overlap near the
same port). Every edge stores evidence ids (capped at 25, with the true count),
time interval, source dataset and quality metadata; the builder refuses an edge without
evidence. Metrics: degree centrality, betweenness (exact on small graphs, 500 seeded
pivots on the full graph), connected components, Louvain communities on the vessel-port
subgraph (7 communities), port-to-port transition counts, and vessel-region pairs seen on
≥ N distinct days. The synthetic scenario test confirms the method recovers a known
two-community structure and identifies the bridge vessel by betweenness.
