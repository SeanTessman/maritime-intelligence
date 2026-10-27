# Investigation case 2: SYNTHETIC demo: impossible position jump

> This application is an educational maritime analytics portfolio project. Anomaly detections represent statistical or rule-based deviations, not evidence of illegal or hostile activity.

*Report type:* anomaly_investigation  
*Analysis period:* 2030-01-07 07:27 to 2030-01-07 07:28 UTC  
*Generated:* 2026-10-19T18:57:18+00:00 (MIADP 0.1.0)

## Data sources
- synthetic_scenario / file n/a

## Methods
- Detections from configurable rules and an Isolation Forest on vessel-day tracks.
- Evidence attached by an analyst; provenance captured when attached.

## Case

Status: Explained. Created 2026-10-19 18:57 UTC by local-analyst.

One fix is ~15-30 km from its neighbours, implying a speed far above any vessel's.

**Status history**

| old_status   | new_status   | changed_by    | reason                                                                     | changed_at                       |
|:-------------|:-------------|:--------------|:---------------------------------------------------------------------------|:---------------------------------|
|              | New          | local-analyst | case opened                                                                | 2026-10-19 18:57:17.983628+00:00 |
| New          | Under review | local-analyst | triage started                                                             | 2026-10-19 18:57:17.999849+00:00 |
| Under review | Explained    | local-analyst | Single-fix outlier consistent with a position error (synthetic injection). | 2026-10-19 18:57:18.003158+00:00 |


## Detections under review

Event 3924 (position_jump): Consecutive positions are 18.46 km apart in 63.0 s (implied 569.6 kn). Possible benign explanations: GNSS or decoding error, or two transmitters configured with the same MMSI. To interpret this, review: vessel static data (name, call sign, dimensions) on each side of the jump. This is a rule-based deviation, not evidence of illegal or hostile activity.

**Anomaly evidence**

|   anomaly_event_id | anomaly_type   | detector   |   anomaly_mmsi | event_time                | score   |
|-------------------:|:---------------|:-----------|---------------:|:--------------------------|:--------|
|               3924 | position_jump  | rule       |      999010151 | 2030-01-07 07:27:49+00:00 |         |


## Supporting observations

**Positions**

|   position_id |   position_mmsi | base_datetime             |     lat |      lon |   sog |
|--------------:|----------------:|:--------------------------|--------:|---------:|------:|
|   1.70902e+06 |       999010151 | 2030-01-07 07:27:49+00:00 | 40.5211 | -74.1878 |  18.2 |
|   1.70902e+06 |       999010151 | 2030-01-07 07:28:52+00:00 | 40.4272 | -74.3678 |  18.4 |


## Analyst notes

**Notes**

| added_at                         | added_by      | note                                                                                                                                             |
|:---------------------------------|:--------------|:-------------------------------------------------------------------------------------------------------------------------------------------------|
| 2026-10-19 18:57:17.997162+00:00 | local-analyst | Single isolated outlier fix; previous and next fixes are consistent with each other. Pattern matches a GNSS/decoding error rather than movement. |


## Evidence references
- anomaly_events.event_id=3924
- ais_positions.position_id=1709023
- ais_positions.position_id=1709024

## Limitations
- AIS is self-reported and received by terrestrial stations; coverage varies and gaps are common. An observation gap is not evidence of deliberate transponder deactivation.
- NOAA Marine Cadastre data is downsampled to roughly one fix per minute per vessel.
- WPI ports are single reference points; 'near port' means within the configured radius of that point, not verified berthing.
- Rule thresholds are heuristics for this study area, not safety or regulatory standards.
- Isolation Forest scores rank statistical unusualness; they are not probabilities of any behaviour and say nothing about intent.