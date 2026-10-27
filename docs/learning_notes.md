# Learning notes

Design decisions, why they were made, what else was considered, and how to defend them.
Written so you can explain the code without notes. Read alongside the code paths cited.

## Milestone 1: foundation and ingestion

**1. Read every CSV column as text, type later** (`ingestion/parse_ais.py`).
*Why:* MMSI and IMO are identifiers, not numbers; `003669999` must stay nine characters.
Typing in one place (`normalize_chunk`) with `errors="coerce"` means one malformed value
becomes NULL and is counted, instead of crashing a 300k-row chunk.
*Alternative:* let pandas infer dtypes. Faster to write, but silently turns MMSIs into
integers and fails unpredictably across files.
*Defend it:* "Identifiers are strings, and parsing failures should be data, not
exceptions."

**2. Hard rejects vs soft flags** (`ingestion/validate_ais.py`).
*Why:* a row with no usable position or vessel id can't be analysed; a row with
heading = 511 is a perfectly good position whose heading is unknown. Dropping it would
throw away ~35% of fixes. The bitmask in `quality_flags` lets every downstream rule
exclude exactly the bad field.
*Defend it:* "Unknown is not zero. I store NULL plus a reason."

**3. Idempotent, resumable loads** (`ingestion/load_database.py`).
*Why:* real pipelines get re-run and interrupted. Three mechanisms: (a) the run is keyed
by file SHA-256 + bbox, so a completed file is skipped; (b) positions have a natural
unique key and use `ON CONFLICT DO NOTHING`; (c) the checkpoint (`last_chunk_done`) and
running quality report are updated in the *same transaction* as the chunk's data.
*Alternative:* truncate-and-reload. Simple but destroys references from cases/evidence.
*Defend it:* walk through `test_ingestion_resumes_after_interruption`.

**4. Filter to the bbox before validating.**
97% of nationwide rows are outside the box. Filtering first cut validation work by ~30x.
The report still counts file-wide invalid coordinates so nothing disappears unreported.

**5. COPY into a temp staging table, then INSERT ... SELECT.**
`COPY` is the fastest way into PostgreSQL but has no conflict handling; staging gives
both speed and `ON CONFLICT`. Measured ~96k raw rows/s end to end on 2 vCPUs.

## Milestone 2: trajectories and geospatial

**6. Reported vs implied speed are separate columns.**
SOG is what the transponder claims; implied speed is distance / time between fixes. Their
disagreement is itself a signal (GNSS error, clock problems), so they must never be
blended. `add_step_metrics` computes implied speed; `sog` is untouched.

**7. Segment at gaps and impossible jumps** (`processing/trajectories.py`).
Drawing a line across a 3-hour gap invents a path. Segments break at gaps > 30 min or
jumps > 100 kn over > 5 km, and every map draws segments separately.

**8. Gap context instead of "dark activity".**
Three geometric labels (near edge, in port, mid-region unexplained). This is the most
important honesty decision in the project: a gap is a fact about the *data*, not the
*vessel*.

**9. Proximity in SQL with gaps-and-islands** (`processing/pipeline.py: PROXIMITY_SQL`).
`ST_DWithin` uses the GIST index to find nearby ports; `LATERAL ... ORDER BY <-> LIMIT 1`
picks the nearest; `LAG` marks a new event when the time gap > 1 h; a running `SUM` of
those marks numbers the "islands". Being able to explain this query is a strong SQL
interview signal.

**10. 2.5 km proximity radius.**
WPI coordinates are rounded to ~1 arc-minute (~1.85 km). A radius smaller than the
rounding error would miss real port activity.

## Milestone 3: detection

**11. Vessel-day as the modelling unit.**
It gives a natural time split, normalises for vessels present all week, and matches the
daily file boundary. Trade-off: voyages crossing midnight are split.

**12. Rules and a model, not one or the other.**
Rules catch specific, explainable point events (a jump, a gap) and come with thresholds an
analyst can argue with. Isolation Forest catches unusual *combinations* at the day level.
The benchmark shows why you need both: rule recall 0.97, model recall at the deployed
threshold 0.13 but average precision 0.87.

**13. Threshold = 99th percentile of training scores, not `contamination`.**
`contamination` would hard-code an assumed anomaly rate. A training-score quantile is an
explicit review budget ("show me the most unusual ~1%"), and the threshold sweep in the
model card shows the precision/recall trade-off.

**14. The density leak I found and fixed.**
Density features were computed over all fixes, so loading the synthetic demo slice changed
real-data features and the model hash. Fix: compute density separately per
`is_synthetic`. Proof: the model version (a hash of the training matrix) returned to its
original value. Good story for "tell me about a bug you found".

**15. Synthetic benchmark realism.**
The first benchmark had a 22% false-positive rate because routes had sharp corners and
transits ended short of their waypoint. Smoothing routes (Chaikin corner cutting) and
landing exactly on the final waypoint removed the artefacts. Lesson: synthetic data can
test your generator as much as your detector.

## Milestone 4: networks

**16. Evidence on every edge, enforced in code** (`networks/graph_builder.py`).
`add_evidence_edge` raises without evidence ids. Edges keep a capped list plus the true
count, so the JSON stays small but the claim stays checkable.

**17. Communities on the vessel-port subgraph only.**
Region nodes connect almost everything and wash out structure. Louvain runs on NEAR_PORT
edges, weighted by evidence count, with a fixed seed.

**18. Sampled betweenness with a seed.**
Exact betweenness is O(VE); 500 seeded pivots keeps the full-graph run to seconds and
reproducible.

## Milestone 5: analyst workspace and API

**19. Provenance captured at attach time.**
If a later re-run changes derived tables, the case still records which dataset, file
hash, detector and model version produced what the analyst saw.

**20. Neutral statuses enforced by the database.**
A CHECK constraint, not just the UI, prevents labels like "confirmed threat".

**21. Read-only API with caps.**
No write surface without auth. Page size ≤ 500 (positions ≤ 5,000), time range ≤ 31 days,
structured errors with request ids, no bulk export.

## Milestone 6: engineering

**22. What was and wasn't verified.**
Native PostgreSQL 16.15 + PostGIS 3.4.2: verified. Docker Compose: syntax-checked only
(registries blocked in the build environment). GitHub Actions: YAML-validated, never run.
Say this plainly in interviews; it builds trust.
