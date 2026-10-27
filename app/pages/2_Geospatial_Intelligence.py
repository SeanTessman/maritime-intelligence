"""Geospatial Intelligence: positions, tracks, ports, density and port proximity for a region/time."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from components.common import SERIES, cached, deck, hex_rgb, page_setup, ports_layer, segment_paths

from maritime import queries
from maritime.processing.vessel_features import vessel_type_group

page_setup("Geospatial Intelligence")
st.title("Geospatial Intelligence")

REGIONS = {
    "Whole study area": (40.3, 41.1, -74.4, -73.4),
    "Upper New York Bay": (40.60, 40.72, -74.10, -73.98),
    "Kill Van Kull / Newark Bay": (40.62, 40.72, -74.20, -74.05),
    "Lower Bay & Ambrose approach": (40.40, 40.62, -74.10, -73.75),
    "East River / western Long Island Sound": (40.69, 40.90, -74.02, -73.70),
    "Hudson River (south)": (40.70, 40.92, -74.05, -73.90),
    "Raritan Bay / Arthur Kill": (40.45, 40.65, -74.32, -74.10),
}
GROUP_COLORS = {
    "cargo": SERIES[0],
    "tanker": SERIES[1],
    "passenger": SERIES[2],
    "tug_tow": SERIES[3],
    "pleasure_sailing": SERIES[4],
    "fishing": SERIES[5],
    "other": SERIES[6],
}


@cached()
def ports() -> pd.DataFrame:
    return queries.ports()


@cached()
def window_positions(bbox: tuple, start: datetime, end: datetime) -> pd.DataFrame:
    df = queries.positions_in_bbox(bbox, start, end, limit=40000)
    df["base_datetime"] = pd.to_datetime(df["base_datetime"], utc=True)
    return df


@cached()
def density(bbox: tuple) -> pd.DataFrame:
    return queries.density_grid(0.005, bbox=bbox)


@cached()
def port_activity() -> pd.DataFrame:
    return queries.port_activity()


f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
region = f1.selectbox("Region", list(REGIONS))
day = f2.date_input(
    "Day (UTC)",
    value=datetime(2023, 1, 11).date(),
    min_value=datetime(2023, 1, 9).date(),
    max_value=datetime(2023, 1, 15).date(),
)
hour = f3.slider("Start hour", 0, 23, 12)
span = f4.selectbox("Window", ["1 h", "3 h", "6 h", "12 h"], index=1)
bbox = REGIONS[region]
start = pd.Timestamp(datetime.combine(day, time(hour)), tz="UTC").to_pydatetime()
end = start + timedelta(hours=int(span.split()[0]))
layers_on = st.multiselect(
    "Layers", ["Tracks", "Positions", "Density (all week)", "Ports"], default=["Tracks", "Ports"]
)

pos = window_positions(bbox, start, end)
pos["group"] = pos["vessel_type"].map(vessel_type_group)
layers = []
if "Density (all week)" in layers_on:
    layers.append(
        pdk.Layer(
            "HeatmapLayer",
            density(bbox),
            get_position=["lon", "lat"],
            get_weight="observations",
            radius_pixels=14,
            threshold=0.03,
        )
    )
if "Ports" in layers_on:
    layers += ports_layer(ports())
if "Tracks" in layers_on and not pos.empty:
    rows = []
    for (mmsi, grp), g in pos.groupby(["mmsi", "group"]):
        g = g.copy()
        g["step_m"] = 0.0
        segs = segment_paths(g, gap_s=1800)
        segs["color"] = [hex_rgb(GROUP_COLORS[grp])] * len(segs)
        segs["tooltip"] = f"{mmsi} {g.vessel_name.iloc[0] or ''} ({grp}) | " + segs["tooltip"]
        rows.append(segs)
    paths = pd.concat(rows, ignore_index=True)
    layers.append(
        pdk.Layer(
            "PathLayer",
            paths,
            get_path="path",
            get_color="color",
            width_min_pixels=2,
            pickable=True,
        )
    )
if "Positions" in layers_on and not pos.empty:
    last = pos.sort_values("base_datetime").groupby("mmsi").tail(1)
    last = last.assign(
        tooltip=last.mmsi
        + " "
        + last.vessel_name.fillna("")
        + " | "
        + last.group
        + " | SOG "
        + last.sog.astype(str)
        + " kn | "
        + last.base_datetime.dt.strftime("%H:%M")
    )
    last["color"] = last["group"].map(lambda g_: hex_rgb(GROUP_COLORS[g_]))
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            last,
            get_position=["lon", "lat"],
            get_radius=120,
            radius_min_pixels=4,
            get_fill_color="color",
            pickable=True,
            stroked=True,
            get_line_color=[26, 26, 25],
            line_width_min_pixels=1,
        )
    )
view = pdk.ViewState(
    latitude=(bbox[0] + bbox[1]) / 2,
    longitude=(bbox[2] + bbox[3]) / 2,
    zoom=9.2 if region == "Whole study area" else 10.6,
)
st.pydeck_chart(deck(layers, view), height=560)
legend = " ".join(f'<span style="color:{c}">&#9632;</span> {g}' for g, c in GROUP_COLORS.items())
st.markdown(
    f"<small>Track colour = AIS vessel-type group: {legend}</small>", unsafe_allow_html=True
)
st.caption(
    f"{len(pos):,} fixes from {pos.mmsi.nunique() if len(pos) else 0} vessels between "
    f"{start:%Y-%m-%d %H:%M} and {end:%H:%M} UTC (spatial query: ST_Intersects on the "
    "geography GIST index; capped at 40,000 fixes)."
)

a, b = st.columns(2)
with a:
    st.subheader("Vessels in view by type")
    if len(pos):
        cnt = pos.groupby("group").mmsi.nunique().sort_values()
        fig = go.Figure(
            go.Bar(
                x=cnt.values,
                y=cnt.index,
                orientation="h",
                marker={"color": [GROUP_COLORS[g] for g in cnt.index], "cornerradius": 4},
                hovertemplate="%{y}: %{x} vessels<extra></extra>",
            )
        )
        fig.update_layout(height=260, xaxis_title="distinct vessels")
        st.plotly_chart(fig, use_container_width=True)
with b:
    st.subheader("Port proximity (whole week)")
    pa = port_activity()
    pa = pa[
        pa.lat.between(bbox[0] - 0.05, bbox[1] + 0.05)
        & pa.lon.between(bbox[2] - 0.05, bbox[3] + 0.05)
    ]
    st.dataframe(
        pa[["port_name", "events", "vessels", "hours_near"]],
        hide_index=True,
        use_container_width=True,
        height=260,
    )
    st.caption(
        "Events = runs of fixes within 2.5 km of the WPI point. Proximity is not a verified "
        "port call. Some WPI points share coordinates (e.g. Tompkinsville / Stapleton)."
    )

st.subheader("Nearest ports to a point")
c1, c2, c3 = st.columns(3)
lat = c1.number_input("Latitude", 40.3, 41.1, 40.65, 0.01, format="%.4f")
lon = c2.number_input("Longitude", -74.4, -73.4, -74.05, 0.01, format="%.4f")
k = c3.slider("k", 1, 10, 5)
st.dataframe(
    queries.nearest_ports(lat, lon, k)
    .assign(distance_km=lambda d: (d.distance_m / 1000).round(2))
    .drop(columns="distance_m"),
    hide_index=True,
    use_container_width=True,
)
st.caption("KNN using the PostGIS geography `<->` operator; distances are geodesic.")
