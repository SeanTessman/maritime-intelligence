# EXPLAIN ANALYZE summary

Measured 2026-10-19 19:13 UTC on PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu, x86_64 (Linux-6.18.44-fc-v139-x86_64-with-glibc2.39), ais_positions = 1,697,405 rows. Single run, warm cache.

| query | execution ms | expected index | index used |
|---|---|---|---|
| trajectory_one_vessel | 14.4 | ix_ais_positions_mmsi_time | yes |
| spatial_bbox_window | 131.1 | ix_ais_positions_geog | yes |
| port_knn | 0.1 | ix_ports_geog | yes |
| proximity_dwithin_one_day | 1493.7 | ix_ports_geog | yes |
| density_grid_full_scan | 499.1 | - | n/a (full scan expected) |

Full plans are in the .txt files next to this summary.
