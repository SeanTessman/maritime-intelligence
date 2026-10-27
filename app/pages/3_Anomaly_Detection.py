"""Anomaly Detection: ranked detections with evidence, explanations and data-quality notes."""

from __future__ import annotations

import json
from datetime import datetime, time

import pandas as pd
import pydeck as pdk
import streamlit as st
from components.common import (
    DETECTOR_COLOR,
    SERIES,
    STATUS,
    cached,
    deck,
    hex_rgb,
    page_setup,
    segment_paths,
    synthetic_badge,
)

from maritime import queries
from maritime.ingestion.validate_ais import describe_flags
from maritime.investigations import cases, evidence

page_setup("Anomaly Detection")
st.title("Anomaly Detection")
st.caption(
    "Ranked by model score, then time. Rule events have no score. Every row is a deviation "
    "to review; none is a finding."
)


@cached(ttl=120)
def load(
    types: tuple, det: str | None, s: datetime, e: datetime, syn_only: bool
) -> tuple[pd.DataFrame, int]:
    return queries.anomalies(
        list(types) or None, det, None, s, e, synthetic_only=syn_only, limit=500
    )


@cached(ttl=600)
def all_types() -> list[str]:
    return queries.anomaly_types()


with st.sidebar:
    syn_only = st.toggle(
        "Show synthetic demo detections instead of real data",
        value=st.query_params.get("synthetic") == "1",
    )
f1, f2, f3 = st.columns([3, 1, 2])
types = f1.multiselect("Anomaly type", all_types())
det = f2.selectbox("Detector", ["any", "rule", "isolation_forest"])
d0, d1 = (
    (datetime(2030, 1, 7).date(), datetime(2030, 1, 13).date())
    if syn_only
    else (datetime(2023, 1, 9).date(), datetime(2023, 1, 15).date())
)
rng = f3.date_input("Event date (UTC)", value=(d0, d1), min_value=d0, max_value=d1)
if isinstance(rng, tuple) and len(rng) == 2:
    d0, d1 = rng
s = pd.Timestamp(datetime.combine(d0, time.min), tz="UTC").to_pydatetime()
e = pd.Timestamp(datetime.combine(d1, time.max), tz="UTC").to_pydatetime()
df, total = load(tuple(types), None if det == "any" else det, s, e, syn_only)
st.markdown(f"**{total:,}** detections match (showing up to 500).")
if df.empty:
    st.info("No detections for these filters.")
    st.stop()

tbl = df[
    [
        "event_id",
        "detector",
        "anomaly_type",
        "score",
        "mmsi",
        "vessel_name",
        "event_time",
        "n_support",
        "quality_notes",
    ]
].copy()
sel = st.dataframe(
    tbl,
    hide_index=True,
    use_container_width=True,
    height=330,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "score": st.column_config.NumberColumn(format="%.3f"),
        "n_support": st.column_config.NumberColumn("supporting fixes"),
    },
)
rows = sel.selection.rows if sel and sel.selection else []
event_id = (
    int(tbl.iloc[rows[0]].event_id)
    if rows
    else int(st.session_state.get("anomaly_event", tbl.event_id.iloc[0]))
)
st.session_state["anomaly_event"] = event_id
st.caption("Select a row to open its evidence.")

d = queries.anomaly_detail(event_id)
if d is None:
    st.stop()
st.divider()
st.subheader(f"Evidence for event {event_id}: {d['anomaly_type']}")
st.markdown(
    synthetic_badge(bool(d["is_synthetic"]))
    + f'<span class="miadp-badge" style="color:{DETECTOR_COLOR[d["detector"]]}">{d["detector"]}</span>'
    f'<span class="miadp-badge">run {d["run_key"]}</span>',
    unsafe_allow_html=True,
)
left, right = st.columns([2, 3])
with left:
    end_s = f" to {d['end_time']:%Y-%m-%d %H:%M:%S}" if pd.notna(d.get("end_time")) else ""
    st.markdown(
        f"**Vessel** {d['mmsi']} {d.get('vessel_name') or ''}  \n"
        f"**When** {d['event_time']:%Y-%m-%d %H:%M:%S}{end_s} UTC"
    )
    if d.get("score") is not None and pd.notna(d.get("score")):
        st.markdown(f"**Model score** {d['score']:.3f} (higher = more unusual; not a probability)")
    st.markdown("**Explanation**")
    st.write(d["explanation"])
    if d.get("quality_notes"):
        st.markdown(
            f"<span style='color:{STATUS['warning']}'>&#9888;</span> **Data-quality / "
            f"interpretation notes:** {d['quality_notes']}",
            unsafe_allow_html=True,
        )
    rf = d.get("rule_flags")
    if isinstance(rf, str):
        rf = json.loads(rf)
    if rf:
        st.markdown("**Measured values vs thresholds**")
        st.json({"values": rf.get("values"), "threshold": rf.get("threshold")}, expanded=True)
    feats = d.get("features")
    if isinstance(feats, str):
        feats = json.loads(feats)
    if feats and feats.get("top_contributors"):
        st.markdown("**Top contributing features** (distance from training median, in IQR units)")
        st.dataframe(
            pd.DataFrame(feats["top_contributors"]).round(3),
            hide_index=True,
            use_container_width=True,
        )
    if st.button("Open this vessel in Vessel Explorer"):
        st.session_state["explorer_mmsi"] = d["mmsi"]
        st.session_state["explorer_q"] = d["mmsi"]
        st.switch_page("pages/1_Vessel_Explorer.py")

with right:
    sp = d["supporting_positions"]
    ctx_start = pd.Timestamp(d["event_time"]) - pd.Timedelta(hours=2)
    ctx_end = pd.Timestamp(
        d["end_time"] if pd.notna(d.get("end_time")) else d["event_time"]
    ) + pd.Timedelta(hours=2)
    ctx, _ = queries.vessel_positions(
        d["mmsi"], ctx_start.to_pydatetime(), ctx_end.to_pydatetime(), limit=5000
    )
    ctx["base_datetime"] = pd.to_datetime(ctx["base_datetime"], utc=True)
    layers = []
    if len(ctx):
        segs = segment_paths(ctx)
        segs["color"] = [hex_rgb(SERIES[0], 170)] * len(segs)
        layers.append(
            pdk.Layer(
                "PathLayer",
                segs,
                get_path="path",
                get_color="color",
                width_min_pixels=2,
                pickable=True,
            )
        )
    if len(sp):
        sp = sp.assign(
            tooltip="evidence fix "
            + sp.position_id.astype(str)
            + " | "
            + pd.to_datetime(sp.base_datetime, utc=True).dt.strftime("%m-%d %H:%M:%S")
            + " | SOG "
            + sp.sog.astype(str)
        )
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                sp,
                get_position=["lon", "lat"],
                get_radius=60,
                radius_min_pixels=5,
                get_fill_color=hex_rgb(SERIES[1]),
                pickable=True,
                stroked=True,
                get_line_color=[26, 26, 25],
                line_width_min_pixels=2,
            )
        )
    lat = float(sp.lat.mean()) if len(sp) else float(d.get("lat") or 40.6)
    lon = float(sp.lon.mean()) if len(sp) else float(d.get("lon") or -74.0)
    st.pydeck_chart(deck(layers, pdk.ViewState(latitude=lat, longitude=lon, zoom=11.5)), height=430)
    st.caption(
        "Orange: supporting fixes stored with the event. Blue: the vessel's track 2 h either "
        "side (split at gaps)."
    )

st.markdown("**Supporting observations**")
if len(sp):
    sp_t = sp.assign(flags=sp.quality_flags.map(lambda f: ", ".join(describe_flags(int(f)))))
    st.dataframe(
        sp_t.drop(columns=["tooltip"], errors="ignore"),
        hide_index=True,
        use_container_width=True,
        height=220,
    )

st.divider()
st.subheader("Add to an investigation case")
cl = cases.list_cases()
c1, c2 = st.columns(2)
with c1:
    opts = ["(new case)"] + [f"{r.case_id}: {r.title} [{r.status}]" for r in cl.itertuples()]
    target = st.selectbox("Case", opts)
    new_title = (
        st.text_input("New case title", value=f"Review {d['anomaly_type']} on {d['mmsi']}")
        if target == "(new case)"
        else None
    )
    inc = st.checkbox("Also attach the supporting fixes as evidence", value=True)
    if st.button("Add detection to case", type="primary"):
        try:
            cid = (
                cases.create_case(new_title)
                if target == "(new case)"
                else int(target.split(":")[0])
            )
            evidence.add_anomaly(cid, event_id, include_positions=inc)
            st.session_state["case_id"] = cid
            st.success(
                f"Added event {event_id} to case {cid}. Open the Investigation Workspace to "
                "continue."
            )
        except cases.CaseError as exc:
            st.error(str(exc))
with c2:
    st.caption(
        "Provenance (source dataset, ingestion file, detector, model version) is captured "
        "automatically when evidence is attached."
    )
