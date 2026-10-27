# Deployment (local)

Two supported paths. **Path B (native PostgreSQL) is the one verified end to end** in the
build environment; Path A (Docker Compose) is written and syntax-checked but has not been
run, because container registries were blocked there.

Requirements: Python 3.11 or 3.12, Git, ~3 GB free disk for one week of raw NOAA files
(~0.3 GB per day) plus ~1 GB for the database. 8 GB RAM is enough: the processing job's
peak resident memory was 2.36 GB for 1.7 M positions (measured in the build environment).

## First-time setup (PowerShell)

```powershell
cd $HOME\Documents\maritime-intelligence
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1           # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python tasks.py setup                  # installs the project + dev tools, creates .env from .env.example
notepad .env                           # set POSTGRES_PASSWORD (local only, never commit .env)
```

Pinned versions: `uv.lock` (uv) or `requirements.lock` (`pip install -r requirements.lock`).

## Path A: Docker Desktop (recommended on Windows)

1. Install Docker Desktop with the WSL2 backend and start it.
2. Database only (use the dashboard from your venv):
   ```powershell
   python tasks.py db-up        # docker compose up -d --wait db  (port bound to 127.0.0.1)
   python tasks.py migrate
   ```
3. Full stack (db + migrations + API + dashboard):
   ```powershell
   docker compose up --build
   ```
   - API http://127.0.0.1:8000/docs, dashboard http://127.0.0.1:8501
   - `migrate` runs once after the DB health check; `api` and `app` wait for it.
   - `./data`, `./models`, `./reports` are mounted, so run the pipeline from the host venv
     (or `docker compose run --rm api python -m maritime.cli pipeline`).
4. Stop: `docker compose down` (keeps the `pgdata` volume). Deleting data requires an
   explicit `docker compose down -v`; no project command does that.

## Path B: native PostgreSQL 16 + PostGIS

Install PostgreSQL 16 with the PostGIS 3.4 bundle (Windows: EDB installer + Stack
Builder -> PostGIS; Linux: `postgresql-16-postgis-3`). Then, as a superuser:

```sql
CREATE ROLE miadp LOGIN PASSWORD '<your password>';
CREATE DATABASE miadp OWNER miadp;
CREATE DATABASE miadp_test OWNER miadp;
\c miadp
CREATE EXTENSION postgis;
\c miadp_test
CREATE EXTENSION postgis;
```

Put the same password in `.env`, then `python tasks.py migrate`. Keep `listen_addresses`
on localhost.

## Data and pipeline

```powershell
python tasks.py download          # NOAA AIS Jan 9-15 2023 into data\raw (HEAD-verified, resumable)
# NGA WPI (3.5 MB) and Natural Earth land (3 MB), if not already present:
curl.exe -L -o data\reference\UpdatedPub150.csv "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv"
python tasks.py synthetic --load  # synthetic fixtures + labelled demo slice (optional but used by the walkthrough)
python tasks.py pipeline          # ingest -> ports -> process -> detect -> evaluate -> network
python scripts\make_sample_investigations.py
```

Each step is idempotent and can be run on its own (`ingest`, `ports`, `process`, `detect`,
`evaluate`, `network`). Ingesting 7 days took about 13 minutes in the build environment (~90 s per daily file).
Use `--start/--end` for other dates, and the `MIADP_BBOX_*` variables for another region.

## Run

```powershell
python tasks.py app     # http://127.0.0.1:8501
python tasks.py api     # http://127.0.0.1:8000/docs
```

## Tests

```powershell
python tasks.py test        # all tests; DB tests use miadp_test and are SKIPPED if it is unreachable
python tasks.py test-unit   # no database needed
python tasks.py lint
```

Set `MIADP_TEST_DATABASE_URL` to point tests at another database. The test fixture rebuilds
the schema from migrations (downgrade base -> upgrade head) on the **test** database only.

## Optional tooling

```powershell
python scripts\explain_queries.py         # EXPLAIN ANALYZE -> sql\explain\
python scripts\benchmark.py               # -> reports\benchmarks\ (needs CREATEDB + PostGIS in miadp_bench)
python -m playwright install chromium     # once, then:
python scripts\capture_screenshots.py     # real screenshots of the running dashboard
python scripts\build_notebook.py          # re-executes notebooks\01_data_exploration.ipynb
```

## Public deployment

Not supported as-is. It would need authentication/authorization, TLS, a secrets manager,
resource limits, rate limiting, removal of the bind-mounted data paths, and a review of
what vessel-level data should be exposed.
