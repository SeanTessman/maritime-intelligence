# Investigation case 1: SYNTHETIC demo: mid-region reporting gap

> This application is an educational maritime analytics portfolio project. Anomaly detections represent statistical or rule-based deviations, not evidence of illegal or hostile activity.

*Report type:* anomaly_investigation  
*Analysis period:* 2030-01-07 04:37 to 2030-01-07 07:39 UTC  
*Generated:* 2026-10-19T18:57:17+00:00 (MIADP 0.1.0)

## Data sources
- synthetic_scenario / file n/a

## Methods
- Detections from configurable rules and an Isolation Forest on vessel-day tracks.
- Evidence attached by an analyst; provenance captured when attached.

## Case

Status: Insufficient evidence. Created 2026-10-19 18:57 UTC by local-analyst.

Synthetic vessel stops reporting mid-harbour for ~3 h and reappears further along its route.

**Status history**

| old_status   | new_status            | changed_by    | reason                                                               | changed_at                       |
|:-------------|:----------------------|:--------------|:---------------------------------------------------------------------|:---------------------------------|
|              | New                   | local-analyst | case opened                                                          | 2026-10-19 18:57:17.792941+00:00 |
| New          | Under review          | local-analyst | triage started                                                       | 2026-10-19 18:57:17.822291+00:00 |
| Under review | Insufficient evidence | local-analyst | Gap alone cannot distinguish coverage loss from a powered-down unit. | 2026-10-19 18:57:17.824667+00:00 |


## Detections under review

Event 3920 (reporting_gap): No observations for 3.02 h while the vessel was moving, away from the study-area edge and ports; it moved 5.59 km across the gap. Possible benign explanations: receiver coverage holes, station outages, data-feed gaps, or a powered-down unit. To interpret this, review: coverage maps for the receiver network, satellite AIS, and whether other vessels in the same area also have gaps at that time. This is a rule-based deviation, not evidence of illegal or hostile activity.

**Anomaly evidence**

|   anomaly_event_id | anomaly_type   | detector   |   anomaly_mmsi | event_time                | score   |
|-------------------:|:---------------|:-----------|---------------:|:--------------------------|:--------|
|               3920 | reporting_gap  | rule       |      999010134 | 2030-01-07 04:37:56+00:00 |         |


## Supporting observations

**Positions**

|   position_id |   position_mmsi | base_datetime             |     lat |      lon |   sog |
|--------------:|----------------:|:--------------------------|--------:|---------:|------:|
|    1.7056e+06 |       999010134 | 2030-01-07 04:37:56+00:00 | 40.5673 | -74.0203 |   7   |
|    1.7056e+06 |       999010134 | 2030-01-07 07:39:10+00:00 | 40.5175 | -74.0293 |   6.7 |


## Analyst notes

**Notes**

| added_at                         | added_by      | note                                                                                                                                                                                                                                                          |
|:---------------------------------|:--------------|:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 2026-10-19 18:57:17.819892+00:00 | local-analyst | Gap is mid-region (not near the study-area edge or a port). Checked neighbouring synthetic vessels: no common outage in this synthetic scenario. In real data the next step would be to check receiver coverage and satellite AIS before drawing conclusions. |


## Evidence references
- anomaly_events.event_id=3920
- ais_positions.position_id=1705599
- ais_positions.position_id=1705600

## Limitations
- AIS is self-reported and received by terrestrial stations; coverage varies and gaps are common. An observation gap is not evidence of deliberate transponder deactivation.
- NOAA Marine Cadastre data is downsampled to roughly one fix per minute per vessel.
- WPI ports are single reference points; 'near port' means within the configured radius of that point, not verified berthing.
- Rule thresholds are heuristics for this study area, not safety or regulatory standards.
- Isolation Forest scores rank statistical unusualness; they are not probabilities of any behaviour and say nothing about intent.