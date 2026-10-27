"""Executive Overview - every number on this page is computed from database records."""

from __future__ import annotations

import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from components.common import (
    SEQ_BLUE,
    SERIES,
    cached,
    deck,
    fmt_int,
    hex_rgb,
    page_setup,
    ports_layer,
)

from maritime import queries

page_setup("Executive Overview")
st.title("Executive Overview")
st.caption(
    "New York Harbor study region, NOAA Marine Cadastre AIS, Jan 9-15 2023. "
    "Synthetic demo data is excluded from every figure on this page."
)


@cached()
def load() -> dict:
    return {
        "counts": queries.overview_counts(),
        "hourly": queries.activity_by_hour(),
        "density": queries.density_grid(0.01),
        "runs": queries.ingestion_runs(),
        "summary": queries.anomaly_summary(),
        "ports": queries.ports(),
        "tables": queries.table_counts(),
    }


d = load()
c = d["counts"]
an = c["anomalies"]
runs = d["runs"]
cols = st.columns(5)
cols[0].metric("Vessels observed", fmt_int(c["vessels"]))
cols[1].metric("AIS observations", fmt_int(c["observations"]))
cols[2].metric("Rule detections", fmt_int(an.get("rule", 0)))
cols[3].metric("Model outlier tracks", fmt_int(an.get("isolation_forest", 0)))
done = runs[runs.status == "completed"]
acc = done.rows_accepted.sum() / max(done.rows_in_bbox.sum(), 1)
cols[4].metric("Accepted (study region)", f"{acc:.3%}")

st.markdown(
    f"**Coverage:** {c['t_min']:%Y-%m-%d %H:%M} to {c['t_max']:%Y-%m-%d %H:%M} UTC &middot; "
    f"lat {c['lat_min']:.2f} to {c['lat_max']:.2f}, lon {c['lon_min']:.2f} to {c['lon_max']:.2f} &middot; "
    f"{fmt_int(done.rows_raw.sum())} raw nationwide rows scanned in {len(done)} daily files"
)

left, right = st.columns([3, 2])
with left:
    st.subheader("Activity over time")
    h = d["hourly"]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=h["hour"],
            y=h["vessels"],
            mode="lines",
            name="Vessels reporting",
            line={"color": SERIES[0], "width": 2},
            hovertemplate="%{x|%a %m-%d %H:00} UTC<br>%{y} vessels<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        hovermode="x unified",
        showlegend=False,
        yaxis_title="vessels reporting per hour",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Distinct MMSIs with at least one fix in each UTC hour.")

    st.subheader("Detections by type (latest run)")
    s = d["summary"]
    if s.empty:
        st.info("No detections yet. Run `python tasks.py detect`.")
    else:
        s = s.sort_values("events")
        fig = go.Figure(
            go.Bar(
                x=s["events"],
                y=s["anomaly_type"],
                orientation="h",
                marker={
                    "color": [
                        SERIES[6] if d_ == "isolation_forest" else SERIES[1] for d_ in s["detector"]
                    ],
                    "cornerradius": 4,
                },
                customdata=s[["vessels", "detector"]],
                hovertemplate="%{y}<br>%{x} events, %{customdata[0]} vessels"
                "<br>detector: %{customdata[1]}<extra></extra>",
            )
        )
        fig.update_layout(height=280, xaxis_title="events", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Orange = rule-based, violet = Isolation Forest. Counts are deviations to review, "
            "not confirmed incidents."
        )

with right:
    st.subheader("Observation density")
    dens = d["density"].copy()
    view = pdk.ViewState(latitude=40.66, longitude=-73.98, zoom=8.7)
    layer = pdk.Layer(
        "HeatmapLayer",
        dens,
        get_position=["lon", "lat"],
        get_weight="observations",
        radius_pixels=18,
        intensity=1.0,
        threshold=0.03,
        color_range=[hex_rgb(x)[:3] for x in SEQ_BLUE[1:]] + [[240, 239, 236]],
    )
    st.pydeck_chart(
        deck([layer, *ports_layer(d["ports"])], view, tooltip={"text": "{tooltip}"}), height=420
    )
    st.caption(
        "Heatmap of fixes on a 0.01 deg grid; amber circles are NGA WPI port points "
        "with the 2.5 km proximity radius."
    )

st.subheader("Recent processing runs")
show = runs[
    [
        "file_name",
        "status",
        "rows_raw",
        "rows_in_bbox",
        "rows_accepted",
        "rows_rejected",
        "rows_duplicate",
        "rows_inserted",
        "finished_at",
    ]
]
st.dataframe(show, hide_index=True, use_container_width=True)

st.subheader("Data-quality summary")
q1, q2 = st.columns(2)
with q1:
    st.dataframe(d["tables"], hide_index=True, use_container_width=True)
    st.caption("Raw table row counts (these include the clearly labelled synthetic demo slice).")
with q2:
    st.markdown(
        f"- Raw rows outside the study box: **{fmt_int(done.rows_raw.sum() - done.rows_in_bbox.sum())}**\n"
        f"- Rows in the study box: **{fmt_int(done.rows_in_bbox.sum())}**\n"
        f"- Hard rejects: **{fmt_int(done.rows_rejected.sum())}**; duplicates: "
        f"**{fmt_int(done.rows_duplicate.sum())}**\n"
        "- Field-level missingness and flags: see *System & Data Health*."
    )
