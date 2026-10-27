-- Indexes created by alembic/versions/0001_initial_schema.py (listed here for reference).
-- Re-running is safe (IF NOT EXISTS).

-- Spatial (GIST) indexes on geography columns: ST_DWithin / ST_Intersects / KNN (<->)
CREATE INDEX IF NOT EXISTS ix_ais_positions_geog   ON ais_positions USING GIST (geog);
CREATE INDEX IF NOT EXISTS ix_ports_geog           ON ports USING GIST (geog);
CREATE INDEX IF NOT EXISTS ix_voyages_track        ON voyages USING GIST (track);

-- Vessel / time composite index: trajectory extraction and LAG/LEAD windows per MMSI
CREATE INDEX IF NOT EXISTS ix_ais_positions_mmsi_time ON ais_positions (mmsi, base_datetime);
CREATE INDEX IF NOT EXISTS ix_ais_positions_time      ON ais_positions (base_datetime);

-- Derived tables
CREATE INDEX IF NOT EXISTS ix_voyages_mmsi_time ON voyages (mmsi, start_time);
CREATE INDEX IF NOT EXISTS ix_ppe_port_time     ON port_proximity_events (port_id, start_time);
CREATE INDEX IF NOT EXISTS ix_ppe_mmsi_time     ON port_proximity_events (mmsi, start_time);
CREATE INDEX IF NOT EXISTS ix_anomaly_time      ON anomaly_events (event_time);
CREATE INDEX IF NOT EXISTS ix_anomaly_mmsi      ON anomaly_events (mmsi, event_time);
CREATE INDEX IF NOT EXISTS ix_anomaly_type      ON anomaly_events (anomaly_type);

-- Uniqueness that makes ingestion idempotent
-- CONSTRAINT uq_ais_positions_obs UNIQUE (mmsi, base_datetime, lat, lon)
