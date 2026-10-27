"""Initial MIADP schema: provenance, AIS observations, ports, derived events, models, cases.

Revision ID: 0001
Revises:
Create Date: 2026-10-19

DDL is written as explicit SQL so every constraint and index is visible in one place
and matches docs/data_dictionary.md.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

-- Every dataset that enters the system (real or synthetic) is registered here.
CREATE TABLE data_sources (
    source_id       SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    publisher       TEXT NOT NULL,
    url             TEXT,
    access_date     DATE,
    coverage_start  DATE,
    coverage_end    DATE,
    license         TEXT,
    is_synthetic    BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (file, region) load attempt. Supports resume via last_chunk_done.
CREATE TABLE ingestion_runs (
    run_id            SERIAL PRIMARY KEY,
    source_id         INTEGER NOT NULL REFERENCES data_sources(source_id),
    file_name         TEXT NOT NULL,
    file_sha256       TEXT NOT NULL,
    bbox_key          TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    last_chunk_done   INTEGER NOT NULL DEFAULT -1,
    rows_raw          BIGINT NOT NULL DEFAULT 0,
    rows_in_bbox      BIGINT NOT NULL DEFAULT 0,
    rows_accepted     BIGINT NOT NULL DEFAULT 0,
    rows_rejected     BIGINT NOT NULL DEFAULT 0,
    rows_duplicate    BIGINT NOT NULL DEFAULT 0,
    rows_inserted     BIGINT NOT NULL DEFAULT 0,
    quality_report    JSONB,
    error_message     TEXT,
    UNIQUE (file_sha256, bbox_key)
);

CREATE TABLE vessels (
    mmsi               TEXT PRIMARY KEY CHECK (mmsi ~ '^[0-9]{9}$'),
    vessel_name        TEXT,
    imo                TEXT,
    call_sign          TEXT,
    vessel_type        INTEGER,
    length_m           REAL,
    width_m            REAL,
    draft_m            REAL,
    cargo              INTEGER,
    transceiver_class  TEXT,
    first_seen         TIMESTAMPTZ,
    last_seen          TIMESTAMPTZ,
    source_id          INTEGER NOT NULL REFERENCES data_sources(source_id),
    is_synthetic       BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_vessels_name ON vessels (vessel_name);

-- Cleaned AIS observations. geog is derived from lat/lon so COPY loads stay simple.
CREATE TABLE ais_positions (
    position_id    BIGSERIAL PRIMARY KEY,
    mmsi           TEXT NOT NULL REFERENCES vessels(mmsi),
    base_datetime  TIMESTAMPTZ NOT NULL,
    lat            DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon            DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    sog            REAL CHECK (sog IS NULL OR sog BETWEEN 0 AND 102.2),
    cog            REAL CHECK (cog IS NULL OR (cog >= 0 AND cog < 360)),
    heading        REAL CHECK (heading IS NULL OR (heading >= 0 AND heading < 360)),
    nav_status     INTEGER,
    draft_m        REAL,
    quality_flags  INTEGER NOT NULL DEFAULT 0,
    source_id      INTEGER NOT NULL REFERENCES data_sources(source_id),
    run_id         INTEGER REFERENCES ingestion_runs(run_id),
    geog           GEOGRAPHY(Point, 4326)
                   GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
    CONSTRAINT uq_ais_positions_obs UNIQUE (mmsi, base_datetime, lat, lon)
);
CREATE INDEX ix_ais_positions_mmsi_time ON ais_positions (mmsi, base_datetime);
CREATE INDEX ix_ais_positions_time ON ais_positions (base_datetime);
CREATE INDEX ix_ais_positions_geog ON ais_positions USING GIST (geog);

CREATE TABLE ports (
    port_id         SERIAL PRIMARY KEY,
    wpi_number      INTEGER UNIQUE,
    port_name       TEXT NOT NULL,
    alternate_name  TEXT,
    unlocode        TEXT,
    country         TEXT,
    region_name     TEXT,
    harbor_size     TEXT,
    harbor_type     TEXT,
    lat             DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon             DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    geog            GEOGRAPHY(Point, 4326)
                    GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
    source_id       INTEGER NOT NULL REFERENCES data_sources(source_id),
    is_synthetic    BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_ports_geog ON ports USING GIST (geog);

-- Trajectory segments: continuous runs of observations with no gap above the threshold.
CREATE TABLE voyages (
    voyage_id       BIGSERIAL PRIMARY KEY,
    mmsi            TEXT NOT NULL REFERENCES vessels(mmsi),
    segment_index   INTEGER NOT NULL,
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ NOT NULL,
    n_obs           INTEGER NOT NULL,
    distance_km     DOUBLE PRECISION,
    start_port_id   INTEGER REFERENCES ports(port_id),
    end_port_id     INTEGER REFERENCES ports(port_id),
    gap_before_s    DOUBLE PRECISION,
    gap_before_context TEXT,
    method          TEXT NOT NULL,
    track           GEOMETRY(LineString, 4326),
    CHECK (end_time >= start_time),
    UNIQUE (mmsi, start_time, method)
);
CREATE INDEX ix_voyages_mmsi_time ON voyages (mmsi, start_time);
CREATE INDEX ix_voyages_track ON voyages USING GIST (track);

-- "Proximity" = observed within radius_m of a port reference point. Not verified docking.
CREATE TABLE port_proximity_events (
    proximity_id     BIGSERIAL PRIMARY KEY,
    mmsi             TEXT NOT NULL REFERENCES vessels(mmsi),
    port_id          INTEGER NOT NULL REFERENCES ports(port_id),
    start_time       TIMESTAMPTZ NOT NULL,
    end_time         TIMESTAMPTZ NOT NULL,
    n_obs            INTEGER NOT NULL,
    min_distance_m   DOUBLE PRECISION NOT NULL,
    radius_m         DOUBLE PRECISION NOT NULL,
    position_ids     BIGINT[] NOT NULL,
    CHECK (end_time >= start_time),
    UNIQUE (mmsi, port_id, start_time, radius_m)
);
CREATE INDEX ix_ppe_port_time ON port_proximity_events (port_id, start_time);
CREATE INDEX ix_ppe_mmsi_time ON port_proximity_events (mmsi, start_time);

CREATE TABLE model_runs (
    model_run_id     SERIAL PRIMARY KEY,
    model_name       TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    params           JSONB NOT NULL,
    feature_list     JSONB NOT NULL,
    train_start      TIMESTAMPTZ,
    train_end        TIMESTAMPTZ,
    eval_start       TIMESTAMPTZ,
    eval_end         TIMESTAMPTZ,
    n_train          INTEGER,
    n_eval           INTEGER,
    data_hash        TEXT,
    artifact_path    TEXT,
    library_versions JSONB,
    metrics          JSONB,
    is_synthetic     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_name, model_version)
);

CREATE TABLE anomaly_events (
    event_id                 BIGSERIAL PRIMARY KEY,
    run_key                  TEXT NOT NULL,
    detector                 TEXT NOT NULL CHECK (detector IN ('rule','isolation_forest')),
    anomaly_type             TEXT NOT NULL,
    mmsi                     TEXT NOT NULL REFERENCES vessels(mmsi),
    event_time               TIMESTAMPTZ NOT NULL,
    end_time                 TIMESTAMPTZ,
    lat                      DOUBLE PRECISION,
    lon                      DOUBLE PRECISION,
    score                    DOUBLE PRECISION,
    rule_flags               JSONB,
    features                 JSONB,
    explanation              TEXT NOT NULL,
    quality_notes            TEXT,
    supporting_position_ids  BIGINT[] NOT NULL DEFAULT '{}',
    model_run_id             INTEGER REFERENCES model_runs(model_run_id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_key, detector, anomaly_type, mmsi, event_time)
);
CREATE INDEX ix_anomaly_time ON anomaly_events (event_time);
CREATE INDEX ix_anomaly_mmsi ON anomaly_events (mmsi, event_time);
CREATE INDEX ix_anomaly_type ON anomaly_events (anomaly_type);

CREATE TABLE investigation_cases (
    case_id      SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'New'
                 CHECK (status IN ('New','Under review','Explained','Insufficient evidence','Closed')),
    created_by   TEXT NOT NULL DEFAULT 'local-analyst',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE case_evidence (
    evidence_id        SERIAL PRIMARY KEY,
    case_id            INTEGER NOT NULL REFERENCES investigation_cases(case_id) ON DELETE CASCADE,
    evidence_type      TEXT NOT NULL CHECK (evidence_type IN ('anomaly','position','proximity','note')),
    anomaly_event_id   BIGINT REFERENCES anomaly_events(event_id),
    position_id        BIGINT REFERENCES ais_positions(position_id),
    proximity_id       BIGINT REFERENCES port_proximity_events(proximity_id),
    note               TEXT,
    provenance         JSONB NOT NULL DEFAULT '{}',
    added_by           TEXT NOT NULL DEFAULT 'local-analyst',
    added_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (evidence_type = 'anomaly'   AND anomaly_event_id IS NOT NULL) OR
        (evidence_type = 'position'  AND position_id IS NOT NULL) OR
        (evidence_type = 'proximity' AND proximity_id IS NOT NULL) OR
        (evidence_type = 'note'      AND note IS NOT NULL)
    )
);
CREATE INDEX ix_case_evidence_case ON case_evidence (case_id);
CREATE UNIQUE INDEX uq_case_evidence_anomaly ON case_evidence (case_id, anomaly_event_id)
    WHERE anomaly_event_id IS NOT NULL;
CREATE UNIQUE INDEX uq_case_evidence_position ON case_evidence (case_id, position_id)
    WHERE position_id IS NOT NULL;

CREATE TABLE case_status_history (
    history_id   SERIAL PRIMARY KEY,
    case_id      INTEGER NOT NULL REFERENCES investigation_cases(case_id) ON DELETE CASCADE,
    old_status   TEXT,
    new_status   TEXT NOT NULL,
    changed_by   TEXT NOT NULL DEFAULT 'local-analyst',
    reason       TEXT,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS case_status_history;
DROP TABLE IF EXISTS case_evidence;
DROP TABLE IF EXISTS investigation_cases;
DROP TABLE IF EXISTS anomaly_events;
DROP TABLE IF EXISTS model_runs;
DROP TABLE IF EXISTS port_proximity_events;
DROP TABLE IF EXISTS voyages;
DROP TABLE IF EXISTS ports;
DROP TABLE IF EXISTS ais_positions;
DROP TABLE IF EXISTS vessels;
DROP TABLE IF EXISTS ingestion_runs;
DROP TABLE IF EXISTS data_sources;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
