-- MIADP analytical queries (PostgreSQL 16 + PostGIS 3.4).
-- The application runs parameterized versions of these from src/maritime/queries.py;
-- :name placeholders are bind parameters. Real-data queries exclude synthetic vessels.

-- 1. Unique vessels and observations ---------------------------------------------------
SELECT COUNT(DISTINCT p.mmsi) AS vessels, COUNT(*) AS observations,
       MIN(p.base_datetime) AS first_fix, MAX(p.base_datetime) AS last_fix
FROM ais_positions p JOIN vessels v USING (mmsi)
WHERE NOT v.is_synthetic;

-- 2. Observation density on a 0.01 degree grid -----------------------------------------
SELECT (floor(lat / 0.01) + 0.5) * 0.01 AS cell_lat, (floor(lon / 0.01) + 0.5) * 0.01 AS cell_lon,
       COUNT(*) AS observations, COUNT(DISTINCT mmsi) AS vessels
FROM ais_positions GROUP BY 1, 2 ORDER BY observations DESC LIMIT 50;

-- 3. Vessel activity by hour of day (UTC) ------------------------------------------------
SELECT EXTRACT(HOUR FROM base_datetime) AS hour_utc, COUNT(*) AS observations,
       COUNT(DISTINCT mmsi) AS vessels
FROM ais_positions GROUP BY 1 ORDER BY 1;

-- 4. Reported speed distribution (1-knot buckets, 0-40 kn) -------------------------------
SELECT width_bucket(sog, 0, 40, 40) - 1 AS sog_kn_floor, COUNT(*) AS n
FROM ais_positions WHERE sog IS NOT NULL GROUP BY 1 ORDER BY 1;

-- 5. Observation gaps per vessel (LAG) ---------------------------------------------------
WITH g AS (
    SELECT mmsi, base_datetime,
           base_datetime - LAG(base_datetime) OVER (PARTITION BY mmsi ORDER BY base_datetime) AS gap
    FROM ais_positions
)
SELECT mmsi, COUNT(*) FILTER (WHERE gap > interval '30 minutes') AS gaps_over_30min,
       MAX(gap) AS longest_gap
FROM g GROUP BY mmsi ORDER BY longest_gap DESC NULLS LAST LIMIT 25;

-- 6. Trajectory extraction for one vessel with step distance (LAG + ST_Distance) ---------
SELECT position_id, base_datetime, lat, lon, sog,
       EXTRACT(EPOCH FROM base_datetime - LAG(base_datetime) OVER w) AS dt_s,
       ST_Distance(geog, LAG(geog) OVER w) AS step_m,
       ST_Distance(geog, LEAD(geog) OVER w) AS next_step_m
FROM ais_positions
WHERE mmsi = :mmsi AND base_datetime BETWEEN :start AND :end
WINDOW w AS (PARTITION BY mmsi ORDER BY base_datetime)
ORDER BY base_datetime;

-- 7. Implied vs reported speed between consecutive fixes ---------------------------------
WITH s AS (
    SELECT mmsi, base_datetime, sog,
           ST_Distance(geog, LAG(geog) OVER w) AS step_m,
           EXTRACT(EPOCH FROM base_datetime - LAG(base_datetime) OVER w) AS dt_s
    FROM ais_positions WINDOW w AS (PARTITION BY mmsi ORDER BY base_datetime)
)
SELECT mmsi, base_datetime, sog AS reported_kn, step_m / dt_s * 1.943844 AS implied_kn
FROM s WHERE dt_s BETWEEN 30 AND 600 AND step_m > 200
  AND abs(step_m / dt_s * 1.943844 - sog) > 15
ORDER BY abs(step_m / dt_s * 1.943844 - sog) DESC LIMIT 25;

-- 8. Nearest ports to a point (KNN on the GIST index) ------------------------------------
SELECT port_id, port_name,
       ST_Distance(geog, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) AS distance_m
FROM ports
ORDER BY geog <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
LIMIT 5;

-- 9. Port proximity events (gaps-and-islands over ST_DWithin) ----------------------------
--    This is the exact statement used by src/maritime/processing/pipeline.py (PROXIMITY_SQL),
--    shown here as a SELECT.
WITH near AS (
    SELECT p.position_id, p.mmsi, p.base_datetime, np.port_id, np.d
    FROM ais_positions p
    CROSS JOIN LATERAL (
        SELECT pt.port_id, ST_Distance(p.geog, pt.geog) AS d FROM ports pt
        WHERE ST_DWithin(p.geog, pt.geog, 2500) ORDER BY p.geog <-> pt.geog LIMIT 1
    ) np
), marked AS (
    SELECT *, CASE WHEN LAG(base_datetime) OVER w IS NULL
                     OR base_datetime - LAG(base_datetime) OVER w > interval '1 hour'
                   THEN 1 ELSE 0 END AS brk
    FROM near WINDOW w AS (PARTITION BY mmsi, port_id ORDER BY base_datetime)
), islands AS (
    SELECT *, SUM(brk) OVER (PARTITION BY mmsi, port_id ORDER BY base_datetime) AS event_no FROM marked
)
SELECT mmsi, port_id, MIN(base_datetime) AS start_time, MAX(base_datetime) AS end_time,
       COUNT(*) AS n_obs, MIN(d) AS min_distance_m
FROM islands GROUP BY mmsi, port_id, event_no ORDER BY start_time LIMIT 50;

-- 10. Port activity summary --------------------------------------------------------------
SELECT p.port_name, COUNT(*) AS proximity_events, COUNT(DISTINCT e.mmsi) AS vessels,
       ROUND(SUM(EXTRACT(EPOCH FROM e.end_time - e.start_time)) / 3600.0, 1) AS hours_near
FROM port_proximity_events e JOIN ports p USING (port_id)
GROUP BY 1 ORDER BY proximity_events DESC;

-- 11. Vessel activity by region (spatial filter with an envelope) ------------------------
SELECT COUNT(DISTINCT mmsi) AS vessels, COUNT(*) AS fixes
FROM ais_positions
WHERE ST_Intersects(geog, ST_MakeEnvelope(-74.10, 40.60, -73.98, 40.72, 4326)::geography);

-- 12. Latest position per vessel before a time (DISTINCT ON) -----------------------------
SELECT DISTINCT ON (mmsi) mmsi, base_datetime, lat, lon, sog
FROM ais_positions WHERE base_datetime <= :at ORDER BY mmsi, base_datetime DESC;

-- 13. Busiest fix per vessel per day (ROW_NUMBER) ---------------------------------------
SELECT * FROM (
    SELECT mmsi, base_datetime::date AS day, base_datetime, sog,
           ROW_NUMBER() OVER (PARTITION BY mmsi, base_datetime::date ORDER BY sog DESC NULLS LAST) AS rn
    FROM ais_positions
) t WHERE rn = 1 ORDER BY sog DESC NULLS LAST LIMIT 25;

-- 14. Anomaly event summary --------------------------------------------------------------
SELECT detector, anomaly_type, COUNT(*) AS events, COUNT(DISTINCT mmsi) AS vessels,
       MIN(event_time) AS first_event, MAX(event_time) AS last_event
FROM anomaly_events GROUP BY 1, 2 ORDER BY events DESC;
