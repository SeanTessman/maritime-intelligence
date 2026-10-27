# CLAUDE.md — Maritime Intelligence & Anomaly Detection Platform (MIADP)

Standing instructions for every session in this repo. Read this file, then
`docs/implementation_status.md`, before doing anything else.

## What this project is

An independently built, unclassified portfolio project by a UConn Statistical
Data Science student (B.S. May 2027, M.S. Data Science Spring 2028). It
ingests real historical NOAA AIS data, stores it in PostgreSQL/PostGIS,
reconstructs vessel trajectories, detects anomalies (rules + Isolation
Forest), builds vessel/port networks, and gives analysts a Streamlit
investigation UI and a FastAPI backend.

It is not affiliated with Palantir, any government agency, or any contractor.
It must not copy their branding, designs, or code.

Required disclaimer, shown on every Streamlit page and in the README:

> This application is an educational maritime analytics portfolio project.
> Anomaly detections represent statistical or rule-based deviations, not
> evidence of illegal or hostile activity.

## Honesty rules (non-negotiable)

- Never claim something was tested, downloaded, measured, or works unless
  you actually ran it in this session and saw the output.
- A skipped test is not a passed test. Report skips separately.
- Never invent URLs, benchmarks, metrics, or screenshots. If something can't
  be measured, write "not measured" and say why.
- Synthetic data always lives in `data/synthetic/`, carries
  `source = 'synthetic'` in the database, and is labeled synthetic in the UI.
  Never present it as NOAA data.
- Precision/recall/F1 only on the synthetic labeled benchmark. For real AIS
  data, report score distributions, stability, and qualitative review only.
- Anomaly scores are not probabilities of wrongdoing. A contamination
  parameter is not a threat rate. An AIS gap is not evidence of deliberate
  transponder shutoff. Being near a port is not verified docking. Two vessels
  near the same port did not necessarily interact.
- Case statuses are limited to: New, Under review, Explained, Insufficient
  evidence, Closed. Never use criminal, hostile, threat, suspect, etc.
- Don't single out real, named vessels as suspicious in docs, reports, or
  sample investigations. Sample investigations use synthetic vessels.

## Data

- Primary: NOAA Marine Cadastre AIS daily files,
  `https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/` (e.g.
  `AIS_2023_01_09.zip`). The 2024 index page still lists files, but as of
  2026-10-19 every 2024 zip returned HTTP 404, so the study moved to 2023
  (see `docs/data_sources.md`). Verify a URL (HEAD request, check size)
  before downloading.
- Study window: **2023-01-09 (Mon) through 2023-01-15 (Sun)**, 7 days.
  Default split: train on Jan 9–13, evaluate on Jan 14–15. Date range and
  bbox are config, not constants.
- Study region (NY Harbor): lat 40.3–41.1, lon -74.4 to -73.4.
- Reference: NGA World Port Index (msi.nga.mil/Publications/WPI). Natural
  Earth only if a milestone needs it.
- Inspect real schemas before writing mappings. Never assume a column exists.
- MMSI and IMO are always strings. Timestamps are UTC (`timestamptz`).
- Raw files go in `data/raw/` and are gitignored. Never commit raw AIS data.
- Record provenance for every dataset in `data_sources` and
  `docs/data_sources.md`: publisher, URL, access date, coverage, license or
  terms, original schema, transforms, known limitations.

## Tech stack

Python 3.11 or 3.12, pandas, NumPy, PyArrow, SQLAlchemy 2.x, psycopg 3,
PostgreSQL 16 + PostGIS (Docker image `postgis/postgis:16-3.4` or the current
equivalent), Alembic, Pydantic v2, GeoPandas, Shapely 2, scikit-learn,
NetworkX, SciPy, Streamlit, Plotly, PyDeck, FastAPI, Uvicorn, pytest, Ruff,
Docker Compose, GitHub Actions.

Dependencies are declared in `pyproject.toml` and locked (`uv.lock` if uv is
available, otherwise a pinned `requirements.lock`). Don't add a dependency
that isn't needed.

## Environment: Windows

- The developer uses Windows with PowerShell and Docker Desktop (WSL2).
- `make` is not available natively. Task runner: `tasks.py`, run as
  `python tasks.py <command>`, with commands such as `setup`, `db-up`,
  `migrate`, `ingest`, `train`, `app`, `api`, `test`, `lint`. A Makefile
  that calls `tasks.py` is optional, for macOS/Linux/CI.
- Use `pathlib` for every path. No hardcoded `/` or `\` separators.
- Setup docs give PowerShell commands first.

## Repository layout

```
maritime-intelligence/
├── app/            Home.py (Executive Overview) + pages/1_..7_ + components/
│   pages: 1_Vessel_Explorer, 2_Geospatial_Intelligence, 3_Anomaly_Detection,
│          4_Network_Analysis, 5_Investigation_Workspace,
│          6_Analytical_Reports, 7_System_Health
├── api/            main.py, routes/, schemas/
├── src/maritime/   config.py, database.py, ingestion/, processing/,
│                   detection/, networks/, investigations/, utils/
├── alembic/        Alembic migrations (alembic.ini at repo root)
├── sql/            analytical_queries.sql, explain/ (EXPLAIN ANALYZE output)
├── data/           raw/ interim/ processed/ reference/ synthetic/  (gitignored except synthetic + .gitkeep)
├── notebooks/      01_data_exploration.ipynb (one notebook is enough)
├── tests/          unit/ integration/ fixtures/ conftest.py
├── reports/        figures/ sample_investigations/ model_cards/ benchmarks/
├── docs/           architecture, data_sources, data_dictionary, methodology,
│                   limitations, api, deployment, interview_walkthrough,
│                   implementation_status, learning_notes
├── scripts/  .github/workflows/  .streamlit/
├── .env.example  .gitignore  docker-compose.yml  Dockerfile
├── pyproject.toml  tasks.py  README.md  LICENSE (MIT)
```

Small, typed, docstringed functions. Clear module boundaries. No frameworks
or abstraction layers the project doesn't need.

## Engineering rules

- Parameterized SQL through SQLAlchemy only. No string-interpolated SQL.
- Bulk loads use PostgreSQL `COPY` (psycopg `copy`), in chunks.
- Ingestion is idempotent and restart-safe: rerunning a file inserts no
  duplicates, and an interrupted run can resume.
- Distances are geodesic (haversine or pyproj). Never treat raw degree
  differences as kilometers. Keep reported speed (SOG) and implied speed
  separate.
- Trajectories are split into segments at large gaps. Never draw a line
  across a gap.
- Logging goes through Python `logging`. Data problems are logged and counted,
  never silently dropped.
- Streamlit queries are cached and paginated. Never send a whole vessel
  population to the browser.
- Ruff for lint and format. Tests run with pytest. DB integration tests use
  the Docker Postgres and are marked `@pytest.mark.db`, so the unit suite
  still runs without Docker.

## Security

- Credentials come from environment variables only. Commit `.env.example`,
  never `.env`.
- Docker ports bind to `127.0.0.1` only. The DB is never exposed publicly.
- No public deployment. Write endpoints stay local; document that auth is
  required before any public use.
- API caps page size (e.g. 500) and time range. No unrestricted bulk export.

## Git

- Commit locally after each step that passes its checks, with clear messages.
- **Never push, add a remote, deploy, or spend money without asking first.**
- Never delete Docker volumes, data, or user files without asking first.

## Session protocol

At the start of a session:
1. Read this file and `docs/implementation_status.md`.
2. Run `git status` and `git log --oneline -10`.
3. Confirm Docker/DB state if the session needs the DB.

During a session, work toward the acceptance criteria in the session prompt.
Run tests as you go, not only at the end.

Before ending a session, or if context is running low:
1. Update `docs/implementation_status.md` with completed items, incomplete
   items, blockers, commands verified, test results (passed/failed/skipped
   counts), record counts processed, known limitations, and exact next steps.
2. Append to `docs/learning_notes.md`: the 3–6 most important design decisions
   from the session, why they were made, alternatives considered, and how to
   defend each in an interview. Write it for the developer, who has to be
   able to explain this code without notes.
3. Commit locally.
4. End with a short report: files changed, features done, commands run, test
   results, data processed, limitations, and what's next.

If you hit a real blocker (no Docker, NOAA unreachable, etc.), build the parts
you can, use a clearly labeled fallback, document the blocker, and keep
going. Ask only about credentials, destructive actions, or real decisions.
