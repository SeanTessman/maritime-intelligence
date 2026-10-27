# Limitations

This is a one-week, one-region educational analysis. Read every output with these in mind.

## Data

- **One week, one harbour.** Jan 9-15 2023, NY Harbor bbox only. Patterns (and model
  scores) may not generalise to other seasons, regions or traffic mixes.
- **2024 data unavailable.** The NOAA 2024 files listed on the index returned HTTP 404 on
  2026-10-19, so 2023 data is used (see `docs/data_sources.md`).
- **NOAA downsampling.** About one fix per minute per vessel; short manoeuvres are
  under-sampled and implied speed over short intervals is noisy.
- **Terrestrial receivers only.** Coverage gaps are normal. No satellite AIS.
- **Self-reported static data.** Ship type, dimensions, name and navigational status come
  from the transponder and are sometimes wrong. IMO is missing for ~40% and draft for
  ~48% of in-region rows.
- **Port reference points.** WPI gives one rounded point per port (not berths or
  terminals); some ports share a point; anchorages and many terminals are absent. Hence
  "near port" ≠ "in port", and "away from port" does not mean "nowhere legitimate".
- **Basemap.** Natural Earth 1:10m land is coarse at harbour scale and is visual context
  only. Online basemap tiles (CARTO) load only when the browser has internet access.

## Methods

- **Thresholds are heuristics.** They were chosen by reasoning about this harbour, not
  fitted or validated against labelled real events (none exist here).
- **Synthetic benchmark is not real-world accuracy.** The injected anomalies and the rules
  share an author; metrics on it verify behaviour, not field performance.
- **Isolation Forest** ranks unusualness relative to the training week. Its scores are not
  probabilities, the 99th-percentile threshold is a review budget, and in-sample scores
  (training days) are optimistic. Vessel-type mix strongly influences what looks unusual
  (fast ferries rank high).
- **Vessel-day tracks** cut at UTC midnight; a voyage crossing midnight is split.
- **Gap context** uses simple geometry (distance to bbox edge and to WPI points); it does
  not model receiver coverage.
- **Network edges are co-observations.** `SHARED_PORT_PROXIMITY` means two vessels were
  near the same port point during overlapping time, nothing more. Community structure on
  a one-week graph is dominated by ferry and tug routes.
- **Betweenness** on the full graph is approximated with 500 seeded pivots.

## Engineering

- **Docker stack not verified in the build environment.** Container registries were
  blocked there, so `docker compose up --build` has been syntax-checked
  (`docker compose config`) but not run. The same code was verified against a native
  PostgreSQL 16.15 + PostGIS 3.4.2 install.
- **GitHub Actions workflow** is written and YAML-validated, but has not run on GitHub
  (nothing has been pushed).
- **Single-user, local-only.** No authentication; the API is read-only; case edits happen
  in the local dashboard. Not production-ready.
- **Benchmarks** are single runs on a 2-vCPU cloud VM (see `reports/benchmarks/`).

## Responsible use

Do not use this project to make claims about real vessels, owners, flags or organisations.
Detections are prompts for review, never findings. Sample investigations use synthetic
vessels only.
