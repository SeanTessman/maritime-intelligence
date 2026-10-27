# Interview walkthrough (5-7 minutes)

Setup before the call: database running, `python tasks.py app` open on the Executive
Overview, `python tasks.py api` running, this file on a second screen. Use the synthetic
demo toggle when showing detections, so you never present a real, named vessel as
"suspicious" on screen.

## Minute 1: the problem

"AIS gives you a position for most commercial vessels about every minute. For one week
in one harbour, NOAA's files contain **55.8 million** nationwide rows; **1.66 million**
fall in my New York Harbor box, from **596** vessels. Nobody can read that by hand. The
analyst's real question is *where should I look first, and why*, with evidence, not
alarms. So I built a pipeline that turns raw public AIS into ranked, explainable,
evidence-backed leads, and is honest about what those leads are not."

## Minute 2: data engineering

Show *System & Data Health*, then `src/maritime/ingestion/`.

- Real NOAA Marine Cadastre daily zips (7 files, 2.2 GB), SHA-256 recorded. The 2024 files
  listed on NOAA's index return 404, so I documented the switch to Jan 9-15 2023 instead
  of inventing a URL.
- Streaming pyarrow reader, everything as text first, so MMSI leading zeros survive and
  one bad value can't break a chunk. Bbox filter before validation.
- Hard rejects with reason codes vs soft quality flags: SOG 102.3 / COG 360 / heading 511
  are "not available", stored as NULL plus a flag, never as zero.
- Load: COPY to staging, `ON CONFLICT DO NOTHING`, checkpoint committed in the same
  transaction. Re-running a file is a no-op; a crash resumes from the last chunk. Both are
  tested (`test_ingestion_is_idempotent`, `test_ingestion_resumes_after_interruption`).
- PostgreSQL 16 + PostGIS: geography points with GIST indexes, `(mmsi, time)` composite
  index. ~96k raw rows/s on a 2-vCPU VM.

## Minute 3: geospatial analysis

*Vessel Explorer* (pick a cargo ship), then *Geospatial Intelligence* (Upper Bay, 3 h).

- Tracks are split at gaps > 30 min or impossible jumps, so the map never draws a line
  across missing data. Blue = reported SOG, orange dots = speed implied by consecutive
  positions; they are separate columns because they answer different questions.
- Distances are geodesic (haversine, tested against the WGS84 geodesic within 0.5%).
- Port proximity is a single SQL statement: `ST_DWithin` + `LATERAL` nearest-port KNN +
  a gaps-and-islands window to merge runs into events (14.5 s for 1.7 M fixes).
- Every gap gets a context: near the region edge, in port, or mid-region unexplained.
  None of those labels claims intent.

## Minute 4: anomaly detection

*Anomaly Detection* with the synthetic toggle on.

- Seven configurable rules (speed for vessel type, implied vs reported speed, mid-region
  gap while moving, abrupt course change, position jump, loitering away from ports,
  peer deviation by robust z-score). Each event stores measured values, the threshold it
  crossed, supporting position ids and data-quality notes.
- Isolation Forest on 22 vessel-day features, trained on Jan 9-13 and scored on Jan 14-15
  (time split, asserted disjoint). Review threshold = 99th percentile of training scores:
  a review budget, not a base rate. Score is not a probability.
- Evaluation: precision/recall only exist on my synthetic labelled benchmark. Rules: 0.97
  recall, 0 false positives, but I designed both the anomalies and the rules, so that
  verifies behaviour, not field accuracy. The model's average precision is 0.87 but recall
  at the deployed threshold is only 0.13: threshold choice dominates. On real data I
  report stability instead: Spearman 0.99 across seeds, no strong train/eval drift
  (KS p = 0.07).

## Minute 5: network analysis

*Network Analysis*: pick a port, then a vessel; click an edge.

- Heterogeneous graph: vessels, ports, grid regions, voyages; 41.5k edges. Every edge
  carries evidence ids, time interval, source and quality, and the builder refuses an edge
  without evidence (tested).
- Centrality, Louvain communities on the vessel-port subgraph, port-to-port transitions.
- Clicking an edge pulls the underlying proximity events from the database. Co-presence
  near a port is labelled as exactly that: not docking, not interaction.
- Synthetic scenario tab: a known two-community structure with a bridge vessel; the code
  recovers both (also a unit test).

## Minute 6: investigation workflow

From an anomaly, "Add detection to case" -> *Investigation Workspace*.

- Neutral statuses only (New / Under review / Explained / Insufficient evidence / Closed),
  enforced by a CHECK constraint. Resolving or closing requires a reason; history is kept.
- Provenance is captured when evidence is attached: dataset, ingestion file and hash,
  detector, run key, model version.
- Export the case as HTML/Markdown/CSV/PDF; every report has period, sources, methods,
  evidence references, limitations and the disclaimer. Two sample cases (synthetic
  vessels) are in `reports/sample_investigations/`.

## Minute 7: engineering decisions and next steps

- Pure DataFrame functions with thin DB jobs, so one code path serves the database, the
  synthetic benchmark and unit tests. 80 automated tests (unit + DB + API).
- Parameterized SQL only; read-only API with caps and structured errors; everything binds
  to localhost; no secrets in the repo.
- What I'd do next: more weeks and seasons, satellite AIS for coverage-aware gap
  analysis, terminal/anchorage polygons instead of WPI points, analyst feedback labels to
  calibrate thresholds, table partitioning by day, and a streaming ingest path.

---

## Ten likely questions

**1. Why not just flag every AIS gap as "going dark"?**
Because most gaps are structural: receiver coverage, the vessel leaving the box, berthing,
feed outages. I classify gap context geometrically and only raise a rule when the gap is
mid-region, > 2 h, and the vessel was moving, and even then the event text says it is not
evidence of deactivation. Distinguishing intent would need coverage maps and
independent sources such as satellite AIS.

**2. How do you know your model works without labels?**
On real data I don't claim accuracy. I show the model is stable (seed Spearman 0.99,
top-20 Jaccard 0.73), that score distributions don't drift much between training and
evaluation days, how often it agrees with the rules, and I review the top-ranked tracks
qualitatively. Labelled metrics come only from a synthetic benchmark, and I'm explicit
that it verifies behaviour, not field performance.

**3. Why Isolation Forest?**
It's unsupervised, cheap, handles mixed-scale features after robust scaling, and gives a
ranking rather than a hard label, which fits "where should I look first". Its weakness is
that it scores whole vessel-days, so single-fix events (a jump, a short reversal) barely
move it; that's why the rules exist and why the benchmark breaks recall down by type.

**4. How did you prevent leakage?**
Time-based split by UTC day (train Jan 9-13, evaluate Jan 14-15) with an assertion that
they don't overlap; the scaler and imputer are fit inside the pipeline on training data
only; synthetic demo vessels are excluded, and density features are computed separately
for synthetic rows. I verified that adding the synthetic slice leaves the model's data
hash unchanged; an earlier version did change it, which is how I found and fixed a
density-feature leak.

**5. Why is ingestion idempotent, and how?**
Re-runs and crashes are normal in operations. Each file is identified by SHA-256 plus
bbox; positions have a natural unique key; loads use `ON CONFLICT DO NOTHING`; the
checkpoint commits atomically with the chunk. Tests simulate a crash after chunk 1 and
check that resume gives the same row count as a clean load.

**6. Why geography instead of geometry?**
Distances in metres on the ellipsoid without choosing a projection, and `ST_DWithin` on
geography uses the GIST index. At harbour scale a local projected CRS would also work and
might be faster; I'd benchmark it before switching.

**7. How would this scale to all U.S. waters for a year?**
~3 billion rows. Partition `ais_positions` by day (declarative partitioning), keep COPY
loads per partition, move feature computation to DuckDB/Polars or Spark over Parquet,
compute proximity per partition in parallel, and serve map views from pre-aggregated
tiles rather than raw points. The code is already chunked and per-file, so the ingest
side parallelises by file.

**8. What does "near a port" mean in your system?**
The nearest WPI reference point is within 2.5 km. WPI coordinates are rounded to about a
nautical mile and some ports share a point, so it can't mean "docked". The UI, API and
graph all call it proximity.

**9. How do you keep SQL injection out?**
All SQL is `text()` with bind parameters; optional filters use constant SQL like
`(CAST(:x AS text) IS NULL OR col = :x)`; the only interpolated identifiers are
module constants. The API validates types, ranges and patterns before anything reaches
the database.

**10. What surprised you in the data?**
How much "anomaly" is really vessel type and data quality. Fast small craft with generic
AIS type codes dominate the model's top ranks; heading is "not available" (511) on about
35% of in-region fixes and COG on about 11%; and a handful of fixes sit at the 102.2 kn
SOG encoding ceiling, which would be the week's "fastest vessel" without a quality note.
Building data-quality flags first made the detections far easier to interpret.

## Design decisions to be ready to defend

- Vessel-day as the modelling unit (time split, comparability, matches file boundary).
- Rules + model together: rules for explainable point events, the model for unusual
  combinations at track level.
- Review threshold from training-score quantile instead of `contamination`.
- Text-first parsing and soft flags vs dropping rows.
- Evidence ids on every edge and detection; provenance captured at attach time.
- Read-only API and neutral case statuses as safety design, not afterthoughts.

## Model limitations (say these before you're asked)

One week, one harbour; thresholds are heuristics; benchmark is self-designed; scores are
relative to the training week; vessel-type codes are self-reported; WPI geometry is coarse.

## Scalability improvements

Day partitions and BRIN on time; parallel per-file ingest; Parquet + DuckDB for features;
incremental feature updates; tile-based map serving; a queue (e.g. Kafka) for live AIS.

## LinkedIn description

> Built an end-to-end maritime analytics platform on real NOAA AIS data: a resumable,
> idempotent ingestion pipeline (56M raw rows -> 1.7M validated positions) into
> PostgreSQL/PostGIS, trajectory reconstruction and port-proximity analysis in SQL, an
> explainable rule engine plus an Isolation Forest with time-split evaluation and a
> model card, an evidence-backed vessel-port network, and a Streamlit investigation
> workspace with a read-only FastAPI backend. 80 automated tests. Educational project on
> public data; detections are leads for review, not findings.

## Resume bullets (all backed by implemented, measured features)

- Built a resumable, idempotent PostgreSQL/PostGIS ingestion pipeline for NOAA AIS data
  (pyarrow streaming, COPY + upsert, per-chunk checkpoints) that processed 55.8M raw rows
  into 1.66M validated positions for 596 vessels at ~96k rows/s, with field-level
  data-quality reporting.
- Developed explainable anomaly detection combining 7 configurable rules with a
  time-split Isolation Forest on 22 vessel-day features; evaluated on a labelled synthetic
  benchmark (rules F1 0.98; model average precision 0.87) and reported seed stability
  (Spearman 0.99) on unlabelled real data.
- Delivered an analyst workflow (Streamlit, 8 pages; FastAPI, 11 read-only endpoints)
  with evidence-backed network analysis (NetworkX, 41.5k edges with provenance), case
  management with neutral statuses, and HTML/PDF/CSV reporting, covered by 80 pytest tests.
