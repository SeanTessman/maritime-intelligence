# API

Read-only FastAPI service. Start with `python tasks.py api`; interactive OpenAPI docs at
http://127.0.0.1:8000/docs and the schema at `/openapi.json`.

## Conventions

- **Pagination:** list endpoints take `limit` (1-500; positions 1-5000) and `offset`, and
  return `{items, total, limit, offset}`.
- **Time filters:** `start`/`end` ISO-8601 (UTC assumed if no offset); `end` must follow
  `start`; maximum range 31 days.
- **Geographic filters:** `min_lat, max_lat, min_lon, max_lon` (all four or none; ranges
  validated).
- **Identifiers:** MMSI must match `^[0-9]{9}$` (422 otherwise).
- **Synthetic data** is hidden unless `include_synthetic=true`.
- **Errors** are JSON: `{"error": "validation_error|http_error|internal_error", "detail": ..., "request_id": "..."}`.
  Every response carries an `x-request-id` header; each request is logged with method,
  path, status and latency.
- **No bulk export** endpoint exists by design.

## Endpoints

| method & path | description |
|---|---|
| `GET /health` | DB/PostGIS connectivity, version, disclaimer |
| `GET /vessels?q=&include_synthetic=&limit=&offset=` | search by MMSI or name substring |
| `GET /vessels/{mmsi}` | vessel metadata + fix count (404 if unknown) |
| `GET /vessels/{mmsi}/positions?start=&end=&limit=&offset=` | ordered fixes with quality flag names |
| `GET /vessels/{mmsi}/features` | vessel-day track features + model score/split |
| `GET /anomalies?anomaly_type=&detector=&mmsi=&start=&end=&min_lat=...&include_synthetic=` | latest-run detections, ranked |
| `GET /anomalies/{event_id}` | detection detail with rule values, model contributors and supporting positions |
| `GET /ports?min_lat=...` | WPI ports |
| `GET /networks/vessels/{mmsi}?relationship_type=&max_nodes=` | 1-hop evidence-backed neighbourhood (max 300 nodes) |
| `GET /ingestion/runs` | ingestion history with counts |
| `GET /cases` | investigation cases (read-only) |

## Examples (PowerShell)

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/vessels?q=MAERSK&limit=5"
Invoke-RestMethod "http://127.0.0.1:8000/anomalies?detector=rule&anomaly_type=reporting_gap&limit=3"
Invoke-RestMethod "http://127.0.0.1:8000/vessels/477737800/positions?start=2023-01-13T00:00:00Z&end=2023-01-14T00:00:00Z&limit=100"
```

## Security

Local development only: binds to 127.0.0.1, read-only, no auth. Before any public
deployment add authentication and authorization (e.g. OIDC), TLS, rate limiting,
per-endpoint cost limits and a secrets manager; and review whether vessel-level data
should be exposed at all. Tests: `tests/integration/test_api.py`.
