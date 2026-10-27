"""Reproducible analytical reports (HTML, Markdown, CSV, PDF).

Every report carries: analysis period, data sources, methods, observations, evidence
references, limitations, the project disclaimer, and a generation timestamp.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from jinja2 import Environment, select_autoescape
from sqlalchemy import Engine, text

from maritime import DISCLAIMER, __version__
from maritime.config import PROJECT_ROOT, get_settings
from maritime.database import get_engine

STANDARD_LIMITATIONS = [
    "AIS is self-reported and received by terrestrial stations; coverage varies and gaps are "
    "common. An observation gap is not evidence of deliberate transponder deactivation.",
    "NOAA Marine Cadastre data is downsampled to roughly one fix per minute per vessel.",
    "WPI ports are single reference points; 'near port' means within the configured radius of "
    "that point, not verified berthing.",
    "Rule thresholds are heuristics for this study area, not safety or regulatory standards.",
    "Isolation Forest scores rank statistical unusualness; they are not probabilities of any "
    "behaviour and say nothing about intent.",
]


@dataclass
class Section:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[tuple[str, pd.DataFrame]] = field(default_factory=list)


@dataclass
class Report:
    title: str
    report_type: str
    analysis_period: str
    data_sources: list[str]
    methods: list[str]
    sections: list[Section]
    evidence_refs: list[str]
    limitations: list[str] = field(default_factory=lambda: list(STANDARD_LIMITATIONS))
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    # ------------------------------------------------------------------ renderers
    def to_markdown(self) -> str:
        out = [
            f"# {self.title}",
            "",
            f"> {DISCLAIMER}",
            "",
            f"*Report type:* {self.report_type}  ",
            f"*Analysis period:* {self.analysis_period}  ",
            f"*Generated:* {self.generated_at} (MIADP {__version__})",
            "",
            "## Data sources",
        ]
        out += [f"- {s}" for s in self.data_sources] + ["", "## Methods"]
        out += [f"- {m}" for m in self.methods]
        for s in self.sections:
            out += ["", f"## {s.heading}", ""] + [p + "\n" for p in s.paragraphs]
            for cap, df in s.tables:
                out += [f"**{cap}**", "", df.to_markdown(index=False) if len(df) else "_none_", ""]
        out += ["", "## Evidence references"] + [f"- {e}" for e in self.evidence_refs or ["none"]]
        out += ["", "## Limitations"] + [f"- {lim}" for lim in self.limitations]
        return "\n".join(out)

    def to_html(self) -> str:
        env = Environment(autoescape=select_autoescape(["html"]))
        return env.from_string(_HTML).render(
            r=self,
            disclaimer=DISCLAIMER,
            version=__version__,
            tables=[
                [
                    (c, df.to_html(index=False, classes="t", border=0, na_rep=""))
                    for c, df in s.tables
                ]
                for s in self.sections
            ],
        )

    def to_csv(self) -> bytes:
        """All tables stacked into one CSV with a 'table' column."""
        frames = [
            df.assign(table=f"{s.heading}: {cap}")
            for s in self.sections
            for cap, df in s.tables
            if len(df)
        ]
        meta = pd.DataFrame(
            [
                {
                    "table": "metadata",
                    "title": self.title,
                    "analysis_period": self.analysis_period,
                    "generated_at": self.generated_at,
                    "disclaimer": DISCLAIMER,
                }
            ]
        )
        return pd.concat([meta, *frames], ignore_index=True).to_csv(index=False).encode()

    def to_pdf(self) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buf = io.BytesIO()
        ss = getSampleStyleSheet()
        doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            title=self.title,
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40,
        )
        esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
        story = [
            Paragraph(esc(self.title), ss["Title"]),
            Paragraph(f"<i>{esc(DISCLAIMER)}</i>", ss["Italic"]),
            Spacer(1, 6),
            Paragraph(
                f"Analysis period: {esc(self.analysis_period)}<br/>Generated: "
                f"{esc(self.generated_at)}",
                ss["Normal"],
            ),
            Paragraph("Data sources", ss["Heading2"]),
        ]
        story += [Paragraph("&bull; " + esc(s), ss["Normal"]) for s in self.data_sources]
        story += [Paragraph("Methods", ss["Heading2"])]
        story += [Paragraph("&bull; " + esc(m), ss["Normal"]) for m in self.methods]
        for s in self.sections:
            story.append(Paragraph(esc(s.heading), ss["Heading2"]))
            story += [Paragraph(esc(p), ss["Normal"]) for p in s.paragraphs]
            for cap, df in s.tables:
                story.append(Paragraph(f"<b>{esc(cap)}</b>", ss["Normal"]))
                if not len(df):
                    story.append(Paragraph("none", ss["Normal"]))
                    continue
                d = df.head(40).astype(str).map(lambda x: x[:40])
                t = Table([list(d.columns)] + d.values.tolist(), repeatRows=1)
                t.setStyle(
                    TableStyle(
                        [
                            ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ]
                    )
                )
                story += [t, Spacer(1, 6)]
        story.append(Paragraph("Evidence references", ss["Heading2"]))
        story += [
            Paragraph("&bull; " + esc(e), ss["Normal"]) for e in self.evidence_refs[:60] or ["none"]
        ]
        story.append(Paragraph("Limitations", ss["Heading2"]))
        story += [Paragraph("&bull; " + esc(lim), ss["Normal"]) for lim in self.limitations]
        doc.build(story)
        return buf.getvalue()


_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ r.title }}</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:1000px;margin:24px auto;
padding:0 16px;color:#111827;line-height:1.5}h1{font-size:1.6rem}h2{font-size:1.15rem;border-bottom:1px solid #e5e7eb;
padding-bottom:4px;margin-top:28px}.disc{background:#fef3c7;border-left:4px solid #d97706;padding:8px 12px}
.meta{color:#4b5563;font-size:.9rem}table.t{border-collapse:collapse;font-size:.8rem;width:100%;overflow-x:auto;display:block}
table.t th,table.t td{border:1px solid #e5e7eb;padding:3px 6px;text-align:left}table.t th{background:#f3f4f6}</style></head>
<body><h1>{{ r.title }}</h1><p class="disc">{{ disclaimer }}</p>
<p class="meta">Report type: {{ r.report_type }} &middot; Analysis period: {{ r.analysis_period }}<br>
Generated {{ r.generated_at }} by MIADP {{ version }}</p>
<h2>Data sources</h2><ul>{% for s in r.data_sources %}<li>{{ s }}</li>{% endfor %}</ul>
<h2>Methods</h2><ul>{% for m in r.methods %}<li>{{ m }}</li>{% endfor %}</ul>
{% for s in r.sections %}<h2>{{ s.heading }}</h2>{% for p in s.paragraphs %}<p>{{ p }}</p>{% endfor %}
{% for cap, html in tables[loop.index0] %}<p><b>{{ cap }}</b></p>{{ html | safe }}{% endfor %}{% endfor %}
<h2>Evidence references</h2><ul>{% for e in r.evidence_refs %}<li>{{ e }}</li>{% else %}<li>none</li>{% endfor %}</ul>
<h2>Limitations</h2><ul>{% for l in r.limitations %}<li>{{ l }}</li>{% endfor %}</ul></body></html>"""


# ---------------------------------------------------------------------- builders
def _sources(engine: Engine) -> list[str]:
    df = pd.read_sql(
        text(
            "SELECT name, publisher, url, access_date, is_synthetic FROM data_sources "
            "ORDER BY source_id"
        ),
        engine,
    )
    return [
        f"{r.publisher} - {r.name}{' (SYNTHETIC)' if r.is_synthetic else ''}; "
        f"{r.url or 'generated locally'}; accessed {r.access_date}"
        for r in df.itertuples()
    ]


def _period(engine: Engine, mmsi: str | None = None) -> str:
    sql = (
        "SELECT min(p.base_datetime), max(p.base_datetime) FROM ais_positions p JOIN vessels v "
        "USING (mmsi) WHERE " + ("p.mmsi = :m" if mmsi else "NOT v.is_synthetic")
    )
    with engine.connect() as c:
        lo, hi = c.execute(text(sql), {"m": mmsi}).one()
    return f"{lo:%Y-%m-%d %H:%M} to {hi:%Y-%m-%d %H:%M} UTC" if lo else "no data"


def vessel_activity_report(mmsi: str, engine: Engine | None = None) -> Report:
    eng = engine or get_engine()
    v = pd.read_sql(text("SELECT * FROM vessels WHERE mmsi = :m"), eng, params={"m": mmsi})
    if v.empty:
        raise ValueError(f"MMSI {mmsi} not found")
    daily = pd.read_sql(
        text(
            """SELECT date_trunc('day', base_datetime)::date AS day, COUNT(*) AS fixes,
                  ROUND(AVG(sog)::numeric, 2) AS mean_sog, MAX(sog) AS max_sog,
                  ROUND(MIN(lat)::numeric, 4) AS min_lat, ROUND(MAX(lat)::numeric, 4) AS max_lat
           FROM ais_positions WHERE mmsi = :m GROUP BY 1 ORDER BY 1"""
        ),
        eng,
        params={"m": mmsi},
    )
    voy = pd.read_sql(
        text(
            """SELECT v.voyage_id, v.start_time, v.end_time, v.n_obs, ROUND(v.distance_km::numeric, 2) AS km,
                  ps.port_name AS start_port, pe.port_name AS end_port, v.gap_before_context
           FROM voyages v LEFT JOIN ports ps ON ps.port_id = v.start_port_id
           LEFT JOIN ports pe ON pe.port_id = v.end_port_id WHERE v.mmsi = :m ORDER BY v.start_time"""
        ),
        eng,
        params={"m": mmsi},
    )
    prox = pd.read_sql(
        text(
            """SELECT e.proximity_id, p.port_name, e.start_time, e.end_time, e.n_obs,
                  ROUND(e.min_distance_m::numeric) AS min_distance_m
           FROM port_proximity_events e JOIN ports p USING (port_id) WHERE e.mmsi = :m
           ORDER BY e.start_time"""
        ),
        eng,
        params={"m": mmsi},
    )
    an = pd.read_sql(
        text(
            "SELECT event_id, detector, anomaly_type, event_time, score FROM anomaly_events "
            "WHERE mmsi = :m ORDER BY event_time"
        ),
        eng,
        params={"m": mmsi},
    )
    meta = v.iloc[0]
    return Report(
        title=f"Vessel activity report - MMSI {mmsi}",
        report_type="vessel_activity",
        analysis_period=_period(eng, mmsi),
        data_sources=_sources(eng),
        methods=[
            "Positions validated and filtered to the study bounding box at ingestion.",
            "Trajectories split at gaps > 30 min or impossible jumps (see methodology).",
            "Port proximity: nearest WPI port within the configured radius (SQL ST_DWithin).",
        ],
        sections=[
            Section(
                "Vessel metadata",
                [
                    f"Name: {meta.vessel_name or 'not reported'}; IMO: {meta.imo or 'not reported'}; "
                    f"call sign: {meta.call_sign or 'not reported'}; AIS type code: {meta.vessel_type}; "
                    f"synthetic: {bool(meta.is_synthetic)}."
                ],
            ),
            Section("Daily activity", [], [("Fixes per UTC day", daily)]),
            Section("Trajectory segments", [f"{len(voy)} segments."], [("Segments", voy)]),
            Section(
                "Port proximity",
                [f"{len(prox)} proximity events (not verified port calls)."],
                [("Proximity events", prox)],
            ),
            Section(
                "Detections",
                [f"{len(an)} rule/model detections reference this vessel."],
                [("Detections", an)],
            ),
        ],
        evidence_refs=[
            f"port_proximity_events.proximity_id={i}" for i in prox["proximity_id"].head(30)
        ]
        + [f"anomaly_events.event_id={i}" for i in an["event_id"].head(30)],
    )


def case_report(case_id: int, engine: Engine | None = None) -> Report:
    from maritime.investigations.cases import get_case, status_history
    from maritime.investigations.evidence import list_evidence

    eng = engine or get_engine()
    case = get_case(case_id, eng)
    if case is None:
        raise ValueError(f"case {case_id} not found")
    ev = list_evidence(case_id, eng)
    hist = status_history(case_id, eng)
    anomalies = ev[ev.evidence_type == "anomaly"][
        ["anomaly_event_id", "anomaly_type", "detector", "anomaly_mmsi", "event_time", "score"]
    ]
    positions = ev[ev.evidence_type == "position"][
        ["position_id", "position_mmsi", "base_datetime", "lat", "lon", "sog"]
    ]
    notes = ev[ev.evidence_type == "note"][["added_at", "added_by", "note"]]
    explanations = [
        f"Event {int(r.anomaly_event_id)} ({r.anomaly_type}): {r.explanation}"
        for r in ev[ev.evidence_type == "anomaly"].itertuples()
    ]
    provs = []
    for p in ev["provenance"].dropna():
        p = p if isinstance(p, dict) else json.loads(p)
        for d in p.get("data", [p]) if isinstance(p, dict) else []:
            if d.get("source"):
                provs.append(f"{d['source']} / file {d.get('file_name') or 'n/a'}")
    period = "n/a"
    times = pd.concat(
        [
            pd.to_datetime(anomalies["event_time"], utc=True),
            pd.to_datetime(positions["base_datetime"], utc=True),
        ]
    ).dropna()
    if len(times):
        period = f"{times.min():%Y-%m-%d %H:%M} to {times.max():%Y-%m-%d %H:%M} UTC"
    return Report(
        title=f"Investigation case {case_id}: {case['title']}",
        report_type="anomaly_investigation",
        analysis_period=period,
        data_sources=sorted(set(provs)) or _sources(eng),
        methods=[
            "Detections from configurable rules and an Isolation Forest on vessel-day tracks.",
            "Evidence attached by an analyst; provenance captured when attached.",
        ],
        sections=[
            Section(
                "Case",
                [
                    f"Status: {case['status']}. Created {case['created_at']:%Y-%m-%d %H:%M} UTC "
                    f"by {case['created_by']}.",
                    case.get("description") or "",
                ],
                [("Status history", hist)],
            ),
            Section("Detections under review", explanations, [("Anomaly evidence", anomalies)]),
            Section("Supporting observations", [], [("Positions", positions)]),
            Section("Analyst notes", [], [("Notes", notes)]),
        ],
        evidence_refs=[f"anomaly_events.event_id={int(i)}" for i in anomalies["anomaly_event_id"]]
        + [f"ais_positions.position_id={int(i)}" for i in positions["position_id"]],
    )


def data_quality_report(engine: Engine | None = None) -> Report:
    eng = engine or get_engine()
    runs = pd.read_sql(
        text(
            """SELECT run_id, file_name, status, rows_raw, rows_in_bbox, rows_accepted, rows_rejected,
                  rows_duplicate, rows_inserted, started_at, finished_at FROM ingestion_runs ORDER BY file_name"""
        ),
        eng,
    )
    reports = pd.read_sql(
        text("SELECT file_name, quality_report FROM ingestion_runs WHERE status='completed'"), eng
    )
    miss, flags = {}, {}
    for r in reports.itertuples():
        q = r.quality_report or {}
        for k, v in (q.get("missing_by_field") or {}).items():
            miss[k] = miss.get(k, 0) + v
        for k, v in (q.get("quality_flags") or {}).items():
            flags[k] = flags.get(k, 0) + v
    tot = max(int(runs["rows_in_bbox"].sum()), 1)
    miss_df = pd.DataFrame(
        [
            {"field": k, "missing": v, "pct_of_study_region_rows": round(100 * v / tot, 2)}
            for k, v in sorted(miss.items(), key=lambda kv: -kv[1])
        ]
    )
    flag_df = pd.DataFrame([{"flag": k, "rows": v} for k, v in flags.items()])
    return Report(
        title="Data-quality report",
        report_type="data_quality",
        analysis_period=_period(eng),
        data_sources=_sources(eng),
        methods=[
            "Chunked streaming parse; hard rejects for unusable rows; soft flags for "
            "unavailable/implausible fields (see validate_ais.py)."
        ],
        sections=[
            Section(
                "Ingestion runs",
                [
                    f"{len(runs)} runs; {int(runs.rows_raw.sum()):,} raw rows; "
                    f"{int(runs.rows_inserted.sum()):,} inserted."
                ],
                [("Runs", runs)],
            ),
            Section("Missing values (study-region rows)", [], [("Missingness", miss_df)]),
            Section("Quality flags", [], [("Flags", flag_df)]),
        ],
        evidence_refs=[f"ingestion_runs.run_id={i}" for i in runs["run_id"]],
    )


def model_evaluation_report(engine: Engine | None = None) -> Report:
    eng = engine or get_engine()
    cards = PROJECT_ROOT / "reports" / "model_cards"
    bench = (
        json.loads((cards / "synthetic_benchmark_results.json").read_text())
        if (cards / "synthetic_benchmark_results.json").exists()
        else {}
    )
    diag = (
        json.loads((cards / "real_data_diagnostics.json").read_text())
        if (cards / "real_data_diagnostics.json").exists()
        else {}
    )
    rows = [
        {
            "detector": k,
            **{
                m: bench[k][m]
                for m in (
                    "precision",
                    "recall",
                    "f1",
                    "false_positive_rate",
                    "tp",
                    "fp",
                    "fn",
                    "tn",
                )
            },
        }
        for k in ("rules", "isolation_forest", "rules_or_model")
        if k in bench
    ]
    runs = pd.read_sql(
        text(
            "SELECT model_run_id, model_name, model_version, n_train, n_eval, "
            "created_at FROM model_runs ORDER BY created_at DESC"
        ),
        eng,
    )
    return Report(
        title="Model evaluation summary",
        report_type="model_evaluation",
        analysis_period=_period(eng),
        data_sources=_sources(eng),
        methods=[
            "Synthetic labeled benchmark with time split (train first 5 days, test last 2).",
            "Real data: seed stability (Spearman, top-k Jaccard), KS shift test, rule/model overlap.",
        ],
        sections=[
            Section(
                "Synthetic benchmark (labels exist only here)",
                [
                    "These metrics are on SYNTHETIC data with injected anomalies designed by the "
                    "author; they verify detector behaviour and are not an estimate of real-world "
                    "accuracy."
                ],
                [
                    ("Metrics", pd.DataFrame(rows)),
                    (
                        "IF threshold sweep",
                        pd.DataFrame(bench.get("isolation_forest_threshold_sweep", [])),
                    ),
                ],
            ),
            Section(
                "Real AIS data (unlabeled)",
                [
                    json.dumps(
                        {
                            k: diag.get(k)
                            for k in ("seed_stability", "train_vs_eval_shift", "eval_flagged")
                        },
                        default=str,
                    )
                ],
                [("Model runs", runs)],
            ),
        ],
        evidence_refs=[f"model_runs.model_run_id={i}" for i in runs["model_run_id"]],
    )


def network_report() -> Report:
    proc = get_settings().processed_dir
    summ = json.loads((proc / "network_summary.json").read_text())
    nodes = pd.read_parquet(proc / "network_nodes.parquet")
    trans = pd.read_parquet(proc / "port_transitions.parquet")
    top_ports = nodes[nodes.node_type == "port"].head(10)[
        ["label", "degree", "betweenness", "community"]
    ]
    return Report(
        title="Network analysis summary",
        report_type="network",
        analysis_period="study window",
        data_sources=["NOAA Marine Cadastre AIS (filtered)", "NGA World Port Index (Pub. 150)"],
        methods=[
            "Heterogeneous NetworkX graph; every edge carries evidence ids.",
            "Degree and (seeded, sampled) betweenness centrality; Louvain communities on the "
            "vessel-port subgraph.",
        ],
        sections=[
            Section("Graph", [json.dumps(summ)]),
            Section("Most central ports", [], [("Ports by betweenness", top_ports)]),
            Section(
                "Frequent port-to-port transitions",
                [],
                [
                    (
                        "Transitions",
                        trans.head(20)[["from_port", "to_port", "transitions", "distinct_vessels"]],
                    )
                ],
            ),
        ],
        evidence_refs=[
            "data/processed/network.json (edge evidence_ids -> port_proximity_events, "
            "ais_positions, voyages)"
        ],
        limitations=STANDARD_LIMITATIONS
        + ["SHARED_PORT_PROXIMITY means co-presence near a port reference point, not interaction."],
    )


def report_metadata(r: Report) -> dict[str, Any]:
    return {
        "title": r.title,
        "type": r.report_type,
        "period": r.analysis_period,
        "generated_at": r.generated_at,
    }
