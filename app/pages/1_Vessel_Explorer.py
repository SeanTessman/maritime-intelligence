"""Vessel Explorer: search a vessel, inspect its track, speed/course, gaps and raw positions."""

from __future__ import annotations

from datetime import datetime, time

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from components.common import (
    SERIES,
    STATUS,
    cached,
    deck,
    hex_rgb,
    page_setup,
    ports_layer,
    segment_paths,
    synthetic_badge,
)

from maritime import queries
from maritime.ingestion.validate_ais import describe_flags
from maritime.processing.vessel_features import vessel_type_group

page_setup("Vessel Explorer")
st.title("Vessel Explorer")


@cached()
def search(q: str, syn: bool) -> pd.DataFrame:
    return queries.search_vessels(q or None, include_synthetic=syn, limit=200)[0]


@cached()
def positions(mmsi: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames, offset = [], 0
    while True:  # page through the API-sized query, capped at 20k fixes for the browser
        df, total = queries.vessel_positions(mmsi, start, end, limit=5000, offset=offset)
        frames.append(df)
        offset += len(df)
        if offset >= total or offset >= 20000 or df.empty:
            break
    out = pd.concat(frames, ignore_index=True)
    out["base_datetime"] = pd.to_datetime(out["base_datetime"], utc=True)
    return out


@cached()
def ports() -> pd.DataFrame:
    return queries.ports()


with st.sidebar:
    syn = st.toggle("Include synthetic demo vessels", value=False)
q = st.text_input(
    "Search by MMSI or vessel name",
    value=st.session_state.get("explorer_q", ""),
    placeholder="e.g. 366 or MAERSK",
    max_chars=64,
)
res = search(q, syn)
if res.empty:
    st.info("No vessels match.")
    st.stop()
res["label"] = (
    res["mmsi"]
    + "  "
    + res["vessel_name"].fillna("(no name)")
    + "  ["
    + res["n_positions"].astype(str)
    + " fixes]"
)
default_mmsi = st.session_state.get("explorer_mmsi")
if default_mmsi in set(res.mmsi):
    idx = int(res.index[res.mmsi == default_mmsi][0])
else:  # default to the busiest cargo/tanker vessel: a more informative first view than a moored ferry
    ships = res.index[res["vessel_type"].between(70, 89)]
    idx = int(ships[0]) if len(ships) else 0
choice = st.selectbox(
    f"{len(res)} matching vessels (ordered by number of fixes)", res["label"], index=idx
)
mmsi = choice.split()[0]
v = queries.vessel(mmsi)

c1, c2 = st.columns([1, 3])
with c1:
    st.markdown(f"### {v.vessel_name or 'Unnamed vessel'}")
    st.markdown(synthetic_badge(bool(v.is_synthetic)), unsafe_allow_html=True)
    st.markdown(
        f"**MMSI** {v.mmsi}  \n**IMO** {v.imo or 'n/a'}  \n**Call sign** {v.call_sign or 'n/a'}  \n"
        f"**AIS type** {v.vessel_type} ({vessel_type_group(v.vessel_type)})  \n"
        f"**Length x width** {v.length_m or '?'} x {v.width_m or '?'} m  \n"
        f"**First / last seen** {v.first_seen:%m-%d %H:%M} / {v.last_seen:%m-%d %H:%M} UTC  \n"
        f"**Fixes in DB** {int(v.n_positions):,}"
    )
    d0, d1 = v.first_seen.date(), v.last_seen.date()
    rng = st.date_input("Date range (UTC)", value=(d0, d1), min_value=d0, max_value=d1)
    if isinstance(rng, tuple) and len(rng) == 2:
        d0, d1 = rng
    gap_min = st.slider("Split track at gaps longer than (min)", 5, 180, 30, 5)
start = pd.Timestamp(datetime.combine(d0, time.min), tz="UTC").to_pydatetime()
end = pd.Timestamp(datetime.combine(d1, time.max), tz="UTC").to_pydatetime()
pos = positions(mmsi, start, end)

with c2:
    if pos.empty:
        st.warning("No positions in this range.")
        st.stop()
    segs = segment_paths(pos, gap_s=gap_min * 60)
    n_seg = len(segs)
    palette = [hex_rgb(SERIES[0] if i % 2 == 0 else SERIES[6]) for i in range(n_seg)]
    segs["color"] = palette
    pos_l = pos.assign(
        tooltip=pos["base_datetime"].dt.strftime("%m-%d %H:%M:%S")
        + " UTC | SOG "
        + pos["sog"].astype(str)
        + " kn | COG "
        + pos["cog"].astype(str)
    )
    layers = [
        *ports_layer(ports()),
        pdk.Layer(
            "PathLayer", segs, get_path="path", get_color="color", width_min_pixels=2, pickable=True
        ),
        pdk.Layer(
            "ScatterplotLayer",
            pos_l,
            get_position=["lon", "lat"],
            get_radius=25,
            radius_min_pixels=1.5,
            get_fill_color=hex_rgb("#f0efec", 140),
            pickable=True,
        ),
    ]
    view = pdk.ViewState(
        latitude=float(pos.lat.median()), longitude=float(pos.lon.median()), zoom=10
    )
    st.pydeck_chart(deck(layers, view), height=470)
    st.caption(
        f"{len(pos):,} fixes drawn as {n_seg} separate segment(s); colours alternate between "
        f"segments. No line is drawn across a gap > {gap_min} min or an impossible jump."
    )

st.subheader("Speed and course over time")
gaps = pos[pos["dt_s"] > gap_min * 60]
fig = go.Figure()
fig.add_trace(
    go.Scattergl(
        x=pos["base_datetime"],
        y=pos["sog"],
        mode="lines",
        name="SOG (reported)",
        line={"color": SERIES[0], "width": 1.5},
        connectgaps=False,
        hovertemplate="%{x|%m-%d %H:%M:%S}<br>SOG %{y} kn<extra></extra>",
    )
)
impl = (pos["step_m"] / pos["dt_s"]) * 3600 / 1852
impl = impl.where(pos["dt_s"].between(20, 900))
fig.add_trace(
    go.Scattergl(
        x=pos["base_datetime"],
        y=impl,
        mode="markers",
        name="implied speed",
        marker={"color": SERIES[1], "size": 3, "opacity": 0.5},
        hovertemplate="%{x|%m-%d %H:%M:%S}<br>implied %{y:.1f} kn<extra></extra>",
    )
)
for g in gaps.itertuples():
    fig.add_vrect(
        x0=g.base_datetime - pd.Timedelta(seconds=g.dt_s),
        x1=g.base_datetime,
        fillcolor=STATUS["warning"],
        opacity=0.12,
        line_width=0,
    )
fig.update_layout(height=280, yaxis_title="knots", hovermode="closest")
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Blue: speed reported by the transponder. Orange dots: speed implied by consecutive "
    "positions (shown for 20 s-15 min intervals). Shaded bands: reporting gaps."
)

fig2 = go.Figure(
    go.Scattergl(
        x=pos["base_datetime"],
        y=pos["cog"],
        mode="markers",
        marker={"color": SERIES[2], "size": 3},
        name="COG",
        hovertemplate="%{x|%m-%d %H:%M:%S}<br>COG %{y} deg<extra></extra>",
    )
)
fig2.update_layout(height=220, yaxis_title="course over ground (deg)", yaxis_range=[0, 360])
st.plotly_chart(fig2, use_container_width=True)

st.subheader("Observation timeline and reporting gaps")
hourly = pos.set_index("base_datetime").resample("1h").size().rename("fixes").reset_index()
fig3 = go.Figure(
    go.Bar(
        x=hourly["base_datetime"],
        y=hourly["fixes"],
        marker={"color": SERIES[0], "cornerradius": 2},
        hovertemplate="%{x|%m-%d %H:00}<br>%{y} fixes<extra></extra>",
    )
)
fig3.update_layout(height=200, yaxis_title="fixes / hour", bargap=0.15)
st.plotly_chart(fig3, use_container_width=True)
if gaps.empty:
    st.caption(f"No gaps longer than {gap_min} min in this range.")
else:
    gt = gaps.assign(
        gap_start=gaps.base_datetime - pd.to_timedelta(gaps.dt_s, unit="s"),
        gap_hours=(gaps.dt_s / 3600).round(2),
        distance_km=(gaps.step_m / 1000).round(2),
    )
    st.dataframe(
        gt[["gap_start", "base_datetime", "gap_hours", "distance_km"]].rename(
            columns={"base_datetime": "gap_end"}
        ),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "A gap means no fixes were received. Receiver coverage, data-pipeline gaps and "
        "transmitter state all produce gaps; a gap is not evidence of intent."
    )

st.subheader("Segments (from the processing job)")
st.dataframe(queries.vessel_segments(mmsi), hide_index=True, use_container_width=True)

st.subheader("Positions")
pos_t = pos.assign(flags=pos["quality_flags"].map(lambda f: ", ".join(describe_flags(int(f)))))
st.dataframe(
    pos_t[
        [
            "position_id",
            "base_datetime",
            "lat",
            "lon",
            "sog",
            "cog",
            "heading",
            "nav_status",
            "dt_s",
            "step_m",
            "flags",
        ]
    ],
    hide_index=True,
    use_container_width=True,
    height=300,
)
st.download_button(
    "Download these positions (CSV)",
    pos_t.to_csv(index=False).encode(),
    file_name=f"positions_{mmsi}.csv",
    mime="text/csv",
)
