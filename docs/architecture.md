# Architecture

```mermaid
flowchart LR
    subgraph Sources
        NOAA["NOAA Marine Cadastre<br/>daily AIS zips"]
        WPI["NGA World Port Index<br/>Pub. 150 CSV"]
        NE["Natural Earth 1:10m land"]
        SYN["Synthetic generators<br/>(labelled, 9990xxxxx MMSIs)"]
    end
    subgraph Ingestion["Ingestion (src/maritime/ingestion)"]
        DL["download_ais<br/>HEAD-verify, resumable"]
        PARSE["parse_ais<br/>pyarrow streaming, text-first"]
        VAL["validate_ais<br/>hard rejects + quality flags"]
        LOAD["load_database<br/>COPY -> staging -> ON CONFLICT<br/>per-chunk checkpoint"]
    end
    DB[("PostgreSQL 16 + PostGIS<br/>vessels / ais_positions / ports<br/>voyages / proximity / anomalies<br/>model_runs / cases / evidence")]
    subgraph Analytics
        TRAJ["processing<br/>trajectories, segments,<br/>gap context, features"]
        PROX["SQL proximity<br/>ST_DWithin + islands"]
        DET["detection<br/>7 rules + Isolation Forest"]
        NET["networks<br/>evidence-backed graph"]
        INV["investigations<br/>cases, provenance, reports"]
    end
    API["FastAPI (read-only)<br/>/vessels /anomalies /ports<br/>/networks /ingestion"]
    UI["Streamlit dashboard<br/>8 pages"]
    NOAA --> DL --> PARSE --> VAL --> LOAD --> DB
    WPI --> DB
    SYN --> LOAD
    DB --> TRAJ --> DB
    DB --> PROX --> DB
    TRAJ --> DET --> DB
    DB --> NET
    DB --> INV --> DB
    DB --> API
    DB --> UI
    NET --> UI
    NE --> UI
```

## Layers

| layer | code | responsibility |
|---|---|---|
| Config | `src/maritime/config.py`, `config/thresholds.yaml`, `.env` | all tunables: DB, dates, bbox, thresholds |
| Storage | `alembic/`, `src/maritime/database.py` | schema migrations, pooled engine (UTC sessions) |
| Ingestion | `src/maritime/ingestion/` | download, parse, validate, load, quality report, ports, Natural Earth |
| Processing | `src/maritime/processing/` | pure-pandas trajectory and feature code + DB job |
| Detection | `src/maritime/detection/` | rules, Isolation Forest, explanations, evaluation, DB job |
| Networks | `src/maritime/networks/` | graph build, metrics, Plotly rendering, DB job |
| Investigations | `src/maritime/investigations/` | cases, evidence with provenance, reports (HTML/MD/CSV/PDF) |
| Queries | `src/maritime/queries.py` | every parameterized analytical query used by API, UI and reports |
| API | `api/` | FastAPI app, Pydantic schemas, routes, structured errors, request logging |
| UI | `app/` | Streamlit pages + shared components |
| CLI / tasks | `src/maritime/cli.py`, `tasks.py` | `python tasks.py <command>` (Windows-friendly) |

## Design decisions

- **Pure functions + thin DB jobs.** Trajectory, feature, rule and model code take
  DataFrames, so the same code runs on the DB extract, the synthetic benchmark and unit
  tests. DB jobs only read, call, and upsert.
- **SQL where the database is better.** Proximity uses PostGIS indexes and window
  functions; the per-vessel trajectory in the UI and API uses `LAG` + `ST_Distance`.
- **Idempotency everywhere.** Natural unique keys on every table that a job writes, so
  every command is safe to re-run.
- **Provenance by construction.** Each position knows its source and ingestion run; each
  detection stores supporting position ids; each graph edge stores evidence ids; each
  case evidence row stores provenance captured when it was attached.
- **Synthetic isolation.** Synthetic rows are flagged at the source level and excluded
  from real-data figures, model training, density features and the real network.
- **Read-only API.** No unauthenticated write surface; case editing is local.

## Data flow sizes (real week)

55.8 M raw rows scanned -> 1.66 M positions (596 vessels) -> 1,844 segments, 14,798
proximity events (incl. synthetic demo) -> 2,787 vessel-day tracks -> 1,236 rule events
+ 32 model-flagged tracks (real) -> graph of 1,809 nodes / 41,551 edges.
