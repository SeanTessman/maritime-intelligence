# Data sources and provenance

Every dataset is registered in the `data_sources` table at load time. Raw files are not
committed (see `.gitignore`); this page records where they came from and what was done
to them.

## 1. NOAA Marine Cadastre AIS (primary)

| | |
|---|---|
| Publisher | NOAA Office for Coastal Management and BOEM, Marine Cadastre program; data from the U.S. Coast Guard Nationwide AIS (NAIS) |
| Index page | https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/index.html |
| Files used | `AIS_2023_01_09.zip` through `AIS_2023_01_15.zip` (7 daily files, 299.9 to 326.6 MB each, 2.19 GB total) |
| Access date | 2026-10-19 |
| Coverage used | 2023-01-09 00:00 to 2023-01-15 23:59:59 UTC (Monday to Sunday) |
| License / terms | U.S. Government public data, distributed by Marine Cadastre with a data-use disclaimer (no warranty; not for navigation) |
| Format | One CSV per zip, nationwide, roughly 1 fix per vessel per minute |

### Why 2023 and not 2024

The original plan targeted `AIS_2024_01_01.zip`. On 2026-10-19 the 2024 index page
still listed every daily file, but **every 2024 zip returned HTTP 404** (both `HEAD` and a
ranged `GET`; `.csv.zst` and `.parquet` variants were also tried). The 2022 and 2023
directories served files normally, so the study moved to the first full week of
2023 after New Year (Jan 9-15). This avoids the Jan 1 holiday and gives seven days for a
time-separated train/evaluate split. The downloader (`src/maritime/ingestion/download_ais.py`)
checks every URL with a `HEAD` request before downloading and never invents URLs.

### SHA-256 of the files used

```
4612e4d562624718bbc94f6bb7eb2160c738f5555fd35225e67c05d68744ee30  AIS_2023_01_09.zip
c40eb547e5b9638af9c29afc238f342c15fe7272db0859aad8a0fbc4e2e74868  AIS_2023_01_10.zip
a85e7d0aee370789e70c24a2585bd6f04d86f094fb78b9b3f5aca7b76631f92b  AIS_2023_01_11.zip
06117400eaf7686bcbb3e049a965b16eddeb8099fbc16312ae52b4fead184639  AIS_2023_01_12.zip
9035412132c3bc1f4feceb12de7c4ea3977c575b50a43c5791260e336469baac  AIS_2023_01_13.zip
79d30531c767e86c66a7a5353fd3908f417e4ce772aec3621557d26cfcaf3475  AIS_2023_01_14.zip
a532f129535f62e3064ca0f3cb3b6f755718f405f6655ff4e2dd10c94646df89  AIS_2023_01_15.zip
```

The same hash is stored per run in `ingestion_runs.file_sha256` and is what makes
re-ingestion idempotent.

### Original schema (inspected from `AIS_2023_01_09.zip`)

`MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign, VesselType,
Status, Length, Width, Draft, Cargo, TransceiverClass` (17 columns). `BaseDateTime` is
ISO-8601 without an offset and is documented by NOAA as UTC. `IMO` is published as
`IMO1234567`.

### Transformation rules

| Source | Target | Rule |
|---|---|---|
| all columns | | read as text first (pyarrow streaming CSV, 32 MB blocks) |
| MMSI | `mmsi` text | trimmed; must match `^[0-9]{9}$` or the row is rejected |
| BaseDateTime | `base_datetime` timestamptz | parsed as UTC; unparseable -> rejected |
| LAT/LON | `lat`,`lon` float8 | out of range, missing, or (0,0) -> rejected; outside study bbox -> counted and skipped |
| SOG | `sog` real | 102.3 -> NULL + `SOG_UNAVAILABLE` flag; < 0 or > 102.3 -> NULL + `SOG_INVALID` |
| COG | `cog` real | 360 -> NULL + `COG_UNAVAILABLE`; outside [0, 360) -> NULL + `COG_INVALID` |
| Heading | `heading` real | 511 -> NULL + `HEADING_UNAVAILABLE`; other out-of-range -> NULL + `HEADING_INVALID` |
| IMO | `vessels.imo` text | `IMO` prefix stripped; `0`/empty -> NULL |
| Length/Width | `length_m`,`width_m` | 0 -> NULL (AIS "not reported") |
| VesselType/Status/Cargo | integer codes | kept as published AIS codes |
| static fields | `vessels` | latest non-null value per MMSI wins; first/last seen widened |

### Results for the study window

7 files, **55,755,505** raw rows scanned, **1,657,401** inside the NY Harbor box,
**1,657,330** inserted (71 duplicates, 0 hard rejects), **596** distinct MMSIs. Per-file
reports are in `reports/data_quality/`.

### Known limitations

- NOAA pre-filters and downsamples the NAIS feed (about 1 fix/min/vessel), so short
  manoeuvres are under-sampled and implied speeds over very short intervals are noisy.
- Terrestrial receivers only: coverage thins offshore and behind terrain. A gap is not
  evidence of transmitter state.
- Static fields (name, type, dimensions) are self-reported and sometimes wrong or missing
  (over the full week, IMO is missing for 39.7% of in-box rows and draft for 48.0%).

## 2. NGA World Port Index (Pub. 150)

| | |
|---|---|
| Publisher | National Geospatial-Intelligence Agency, Maritime Safety Information |
| URL | https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv (linked from https://msi.nga.mil/Publications/WPI) |
| Access date | 2026-10-19 |
| License / terms | U.S. Government public data |
| Size | 3.5 MB CSV, 3,807 ports, 100+ columns |
| Used | 41 ports within the study bbox plus a 0.5 degree buffer |

Columns used: `World Port Index Number, Main Port Name, Alternate Port Name, UN/LOCODE,
Country Code (holds the country name), Region Name, Harbor Size, Harbor Type, Latitude,
Longitude`. Rows missing a number, name or coordinates are dropped (none in the study area).

Limitations: each port is a single point rounded to about one arc-minute (~1.85 km), not a
berth or terminal polygon; some points coincide (Tompkinsville SI and Stapleton SI share
coordinates). This is why the proximity radius is 2.5 km and why "near port" never means
"docked".

## 3. Natural Earth 1:10m land (map context only)

| | |
|---|---|
| Publisher | Natural Earth (naturalearthdata.com) |
| URL | https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip |
| Access date | 2026-10-19 |
| License | Public domain |
| Used | clipped to the study bbox + 0.6 degrees -> `assets/natural_earth_ny_land.geojson` (24 KB, committed) |

Used only as a background layer so maps keep geographic context when basemap tiles are
unavailable. 1:10m generalisation does not resolve narrow channels or piers; it is never
used in any calculation.

## 4. Synthetic data (clearly labelled)

| Product | Where | Purpose |
|---|---|---|
| NOAA-schema fixture with known bad rows | `tests/fixtures/synthetic_ais_small.csv` | ingestion tests, CI |
| Labeled anomaly benchmark (200 vessels x 7 days, 10% injected anomalies) | `data/synthetic/benchmark_*` | detector evaluation metrics |
| Network scenario (2 groups + 1 bridge vessel) | generated in code | graph tests, demo tab |
| Demo slice loaded into the DB | `vessels.is_synthetic = true` | investigation walkthrough and sample cases |

All synthetic vessels use MMSIs in the `9990xxxxx` block (first digit 9 is not a
ship-station MID, so they cannot collide with a real vessel) and timestamps in January
2030. Synthetic rows are registered under `synthetic_ais_fixture` / `synthetic_scenario`
with `is_synthetic = true`, are excluded from every real-data figure, model and network,
and carry a SYNTHETIC badge in the UI.

## How the data reached the cloud build environment

The cloud workspace used to build this project could not reach coast.noaa.gov, msi.nga.mil
or naturalearthdata.com (outbound policy). The files were downloaded on the developer's
laptop with `curl`, their SHA-256 hashes recorded there, then copied to the build
environment and re-verified (all seven hashes matched). On a normal machine,
`python tasks.py download` performs the same download directly.
