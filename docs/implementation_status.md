# Implementation status

Last updated: 2026-10-19 (end of the initial build session).

## Milestones

| # | milestone | status | evidence |
|---|---|---|---|
| 1 | Foundation: repo, env, PostGIS, migrations, NOAA ingestion, validation, SQL, vessel explorer | **complete** | 7 real NOAA files ingested; re-run is a no-op; crash-resume tested; Vessel Explorer renders real tracks |
| 2 | Geospatial: trajectories, distances, WPI ports, proximity, maps | **complete** | chronological ordering, gap segmentation, SQL proximity reproducible (re-run gives identical counts), spatial tests pass |
| 3 | Detection: rules, features, Isolation Forest, synthetic benchmark, model card | **complete** | reproducible model hash; benchmark metrics; real-data stability diagnostics; model card |
| 4 | Network: graph, evidence, metrics, explorer | **complete** | every edge has evidence (enforced); synthetic structure recovered; explorer page |
| 5 | Analyst workspace: cases, notes, evidence, reports, API | **complete** | cases saved/reloaded in DB; reports cite sources; 11 API endpoints tested |
| 6 | Final engineering & portfolio | **complete, with two unverified items** | see "Not verified" below |

## Verified commands (run in the build environment)

```
alembic upgrade head                      # schema on PostgreSQL 16.15 + PostGIS 3.4.2
python -m maritime.cli ingest             # 7 files, 55,755,505 raw rows -> 1,657,330 inserted
python -m maritime.cli ingest             # second run: every file "skipped_already_completed"
python -m maritime.cli ports              # 41 WPI ports
python -m maritime.cli synthetic --load   # fixtures + 40,075 synthetic demo positions
python -m maritime.cli process            # 2,035 voyages, 14,798 proximity events, 2,787 tracks
python -m maritime.cli detect             # 1,236 real rule events, 237 synthetic, 32 model-flagged tracks
python -m maritime.cli evaluate           # synthetic benchmark metrics
python -m maritime.cli network            # 1,809 nodes, 41,551 edges
python scripts/make_sample_investigations.py
python scripts/explain_queries.py
python scripts/benchmark.py
python scripts/build_notebook.py
python scripts/capture_screenshots.py     # 8 pages, 0 Streamlit exceptions
pytest                                    # 80 passed, 0 failed, 0 skipped
ruff check . ; ruff format --check .      # clean
docker compose config -q                  # compose file valid (syntax only)
```

## Test results

80 passed, 0 failed, 0 skipped (local run against `miadp_test`). Also verified from a fresh
`git clone` of the repository into an empty directory with a new virtual environment (see
the completion report in the chat / commit history).

## Not verified (blocked in the build environment)

- **`docker compose up --build`**: Docker Hub and other registries were unreachable, so the
  images could not be pulled. The compose file passes `docker compose config`; the same
  application code was verified against native PostgreSQL/PostGIS.
- **GitHub Actions**: workflow written and YAML-parsed; it has never run because nothing
  has been pushed (pushing requires your authorization).

## Known limitations

See `docs/limitations.md`. Main ones: one week/one harbour; 2023 data because NOAA 2024
files 404; heuristic thresholds; author-designed benchmark; Natural Earth basemap is
coarse; no authentication (local only).

## Pending decisions for the developer

1. Create a GitHub repository and push (`git remote add origin ...; git push -u origin main`).
   Then check the CI run.
2. Install Docker Desktop and run `docker compose up --build` once; fix anything it shows.
3. Optionally widen the study (more weeks) and re-tune thresholds on the new distributions.

## Exact next steps (for a future session)

1. Read `CLAUDE.md` and this file; `git log --oneline -15`.
2. On the laptop: `python tasks.py setup`, set `.env`, start PostGIS (Docker or native),
   `python tasks.py migrate`, then `python tasks.py pipeline` (raw files are already in
   `data\raw`, reference CSV in `data\reference`).
3. `python tasks.py test` and `python tasks.py app`; compare counts with this file.
4. Verify Docker Compose and CI as above and update this file.
