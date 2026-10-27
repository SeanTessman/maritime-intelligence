"""Shared Streamlit helpers: page setup, disclaimer, cached data access, chart + map styling."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import plotly.io as pio  # noqa: E402
import pydeck as pdk  # noqa: E402
import streamlit as st  # noqa: E402

from maritime import DISCLAIMER  # noqa: E402
from maritime.database import check_connection, get_engine  # noqa: E402

# Validated dark-mode categorical palette (dataviz reference instance), fixed order.
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK = {
    "primary": "#f0efec",
    "secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "surface": "#1a1a19",
}
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

DETECTOR_COLOR = {"rule": SERIES[1], "isolation_forest": SERIES[6]}
MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
NY_VIEW = pdk.ViewState(latitude=40.62, longitude=-73.98, zoom=9.2, pitch=0)

pio.templates["miadp"] = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "system-ui, -apple-system, Segoe UI, sans-serif",
            "color": INK["secondary"],
            "size": 12,
        },
        colorway=SERIES,
        xaxis={"gridcolor": INK["grid"], "linecolor": INK["axis"], "zeroline": False},
        yaxis={"gridcolor": INK["grid"], "linecolor": INK["axis"], "zeroline": False},
        hoverlabel={"bgcolor": "#262624", "font": {"color": INK["primary"]}},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
)
pio.templates.default = "plotly_dark+miadp"

CSS = """
<style>
.block-container {padding-top: 1.6rem; max-width: 1400px;}
div[data-testid="stMetric"] {background: #1a1a19; border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px; padding: 10px 14px;}
div[data-testid="stMetricLabel"] p {color: #c3c2b7; font-size: 0.8rem; text-transform: uppercase;
  letter-spacing: .04em;}
.miadp-disclaimer {border-left: 3px solid #fab219; background: #1f1c14; color: #c3c2b7;
  padding: 6px 10px; font-size: 0.82rem; border-radius: 4px; margin-bottom: 0.6rem;}
.miadp-badge {display:inline-block; padding:1px 8px; border-radius:10px; font-size:.75rem;
  border:1px solid rgba(255,255,255,.15); color:#c3c2b7; margin-right:4px;}
.miadp-synthetic {border-color:#c98500; color:#fab219;}
</style>
"""


def page_setup(title: str, icon: str = ":material/radar:", need_db: bool = True) -> None:
    st.set_page_config(page_title=f"MIADP - {title}", page_icon=icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="miadp-disclaimer">{DISCLAIMER}</div>', unsafe_allow_html=True)
    with st.sidebar:
        st.caption("Maritime Intelligence & Anomaly Detection Platform")
        st.caption("Educational, unclassified portfolio project. Public NOAA/NGA data.")
    if need_db:
        ok, msg = db_status()
        if not ok:
            st.error(
                f"Cannot reach the database ({msg}). Start it with `python tasks.py db-up`, "
                "then `python tasks.py migrate`. See docs/deployment.md."
            )
            st.stop()


@st.cache_data(ttl=30, show_spinner=False)
def db_status() -> tuple[bool, str]:
    return check_connection(get_engine())


def cached(ttl: int = 600):  # noqa: ANN201
    """st.cache_data with a sensible default TTL for DB-backed queries."""
    return st.cache_data(ttl=ttl, show_spinner="Querying database...")


def synthetic_badge(is_synthetic: bool) -> str:
    return (
        '<span class="miadp-badge miadp-synthetic">SYNTHETIC</span>'
        if is_synthetic
        else '<span class="miadp-badge">NOAA AIS</span>'
    )


def hex_rgb(h: str, alpha: int = 255) -> list[int]:
    h = h.lstrip("#")
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)] + [alpha]


@st.cache_data(show_spinner=False)
def _land_geojson() -> dict | None:
    import json

    path = ROOT / "assets" / "natural_earth_ny_land.geojson"
    return json.loads(path.read_text()) if path.exists() else None


def land_layer() -> pdk.Layer | None:
    """Natural Earth land outline so maps keep geographic context even without basemap tiles."""
    gj = _land_geojson()
    if gj is None:
        return None
    return pdk.Layer(
        "GeoJsonLayer",
        gj,
        filled=True,
        stroked=True,
        get_fill_color=[38, 38, 36, 150],
        get_line_color=[137, 135, 129, 160],
        line_width_min_pixels=1,
        pickable=False,
    )


def deck(
    layers: list[pdk.Layer],
    view: pdk.ViewState | None = None,
    tooltip: dict[str, Any] | None = None,
) -> pdk.Deck:
    base = [lyr for lyr in [land_layer()] if lyr is not None]
    return pdk.Deck(
        layers=base + layers,
        initial_view_state=view or NY_VIEW,
        map_style=MAP_STYLE,
        tooltip=tooltip or {"text": "{tooltip}"},
    )


def ports_layer(ports: pd.DataFrame, radius_m: float = 2500) -> list[pdk.Layer]:
    df = ports.assign(tooltip="Port (WPI): " + ports["port_name"].astype(str))
    return [
        pdk.Layer(
            "ScatterplotLayer",
            df,
            get_position=["lon", "lat"],
            get_radius=radius_m,
            get_fill_color=hex_rgb(SERIES[3], 18),
            get_line_color=hex_rgb(SERIES[3], 120),
            stroked=True,
            line_width_min_pixels=1,
            pickable=False,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            df,
            get_position=["lon", "lat"],
            get_radius=120,
            radius_min_pixels=4,
            get_fill_color=hex_rgb(SERIES[3]),
            pickable=True,
        ),
    ]


def segment_paths(pos: pd.DataFrame, gap_s: float = 1800, jump_kn: float = 100) -> pd.DataFrame:
    """Split a vessel's fixes into path segments at gaps/jumps so no line crosses a gap."""
    if pos.empty:
        return pd.DataFrame(columns=["segment", "path", "tooltip"])
    p = pos.sort_values("base_datetime").reset_index(drop=True)
    dt = p["base_datetime"].diff().dt.total_seconds()
    step = p["step_m"] if "step_m" in p else pd.Series(0.0, index=p.index)
    implied = (step / dt) * 3600 / 1852
    brk = (dt > gap_s) | ((implied > jump_kn) & (step > 5000))
    p["segment"] = brk.fillna(False).astype(int).cumsum()
    rows = []
    for seg, g in p.groupby("segment"):
        rows.append(
            {
                "segment": int(seg),
                "path": g[["lon", "lat"]].values.tolist(),
                "n": len(g),
                "tooltip": f"segment {seg}: {g.base_datetime.iloc[0]:%m-%d %H:%M} to "
                f"{g.base_datetime.iloc[-1]:%m-%d %H:%M} UTC ({len(g)} fixes)",
            }
        )
    return pd.DataFrame(rows)


def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "n/a"
