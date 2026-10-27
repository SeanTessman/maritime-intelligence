# Benchmark results

Measured 2026-10-19T19:15:02+00:00 on `Linux-6.18.44-fc-v139-x86_64-with-glibc2.39`, 2 vCPU, 8.4 GB RAM, PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu, ais_positions = 1,697,405 rows.

These are single-machine measurements from `scripts/benchmark.py`; rerun on your own hardware.

```json
{
  "dataset": {
    "ais_positions_rows": 1697405,
    "postgres": "PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu"
  },
  "ingestion": {
    "file": "AIS_2023_01_09.zip",
    "file_mb": 326.6,
    "seconds": 86.5,
    "raw_rows": 8323136,
    "inserted_rows": 246101,
    "raw_rows_per_s": 96265,
    "method": "single run: stream zip -> validate -> bbox filter -> COPY + upsert, fresh schema"
  },
  "queries": {
    "trajectory_retrieval": {
      "vessels": 20,
      "median_ms": 18.0,
      "max_ms": 152.6,
      "median_fixes_returned": 904,
      "method": "vessel_positions() incl. LAG/ST_Distance, up to 5,000 fixes"
    },
    "spatial_bbox_3h": {
      "reps": 10,
      "median_ms": 138.2,
      "min_ms": 108.2,
      "max_ms": 226.4,
      "method": "ST_Intersects envelope + 3 h window"
    },
    "port_knn": {
      "reps": 20,
      "median_ms": 2.4,
      "min_ms": 1.9,
      "max_ms": 5.0,
      "method": "geography <-> KNN, k=5"
    }
  },
  "processing": {
    "proximity_sql_full_table": {
      "seconds": 14.5,
      "positions": 1697405,
      "events": 14798,
      "method": "PROXIMITY_SQL (ST_DWithin + LATERAL KNN + islands), upsert, single run"
    },
    "trajectory_steps": {
      "seconds": 7.2,
      "positions": 1697405,
      "method": "compute_steps(), single run"
    },
    "track_features": {
      "seconds": 21.3,
      "tracks": 2787,
      "method": "compute_track_features(), single run"
    }
  },
  "dashboard_load": {
    "page": "Executive Overview",
    "runs_ms": [
      11672,
      5674,
      1225
    ],
    "method": "headless Chromium, navigation until first metric + first Plotly chart rendered; run 1 is a cold Streamlit cache, runs 2-3 are cached"
  }
}
```
