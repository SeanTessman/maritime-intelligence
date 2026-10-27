"""Network Analysis: focused, evidence-backed vessel / port / region relationships."""

from __future__ import annotations

import json
from datetime import datetime, time

import networkx as nx
import pandas as pd
import streamlit as st
from components.common import cached, page_setup
from sqlalchemy import text

from maritime.config import get_settings
from maritime.database import get_engine
from maritime.networks import graph_metrics as gm
from maritime.networks.graph_builder import RELATIONSHIP_TYPES, build_graph, ego_graph, filter_graph
from maritime.networks.pipeline import load_network, network_path
from maritime.networks.visualization import ego_figure
from maritime.synthetic import make_network_scenario

page_setup("Network Analysis")
st.title("Network Analysis")
st.caption(
    "Edges are co-observations backed by evidence records. Proximity to a port point is not "
    "docking, and two vessels near the same port at once did not necessarily interact."
)


@st.cache_resource(show_spinner="Loading network...")
def graph(mtime: float) -> nx.Graph:
    return load_network()


@cached()
def node_table() -> pd.DataFrame:
    return pd.read_parquet(get_settings().processed_dir / "network_nodes.parquet")


@cached()
def transitions() -> pd.DataFrame:
    return pd.read_parquet(get_settings().processed_dir / "port_transitions.parquet")


tab_real, tab_syn = st.tabs(
    ["Study-area network (NOAA AIS)", "Synthetic scenario (known structure)"]
)

with tab_real:
    if not network_path().exists():
        st.warning("Network not built yet. Run `python tasks.py network`.")
        st.stop()
    G = graph(network_path().stat().st_mtime)
    summary = json.loads((get_settings().processed_dir / "network_summary.json").read_text())
    m = st.columns(5)
    m[0].metric("Nodes", f"{summary['nodes']:,}")
    m[1].metric("Edges", f"{summary['edges']:,}")
    m[2].metric("Components", summary["connected_components"])
    m[3].metric("Largest component", f"{summary['largest_component_size']:,}")
    m[4].metric("Vessel-port communities", summary["communities_vessel_port"])

    nodes = node_table()
    real_nodes = [n for n, d in G.nodes(data=True) if not d.get("is_synthetic")]
    c1, c2 = st.columns([2, 3])
    with c1:
        kind = st.radio("Focus entity type", ["vessel", "port"], horizontal=True)
        cand = nodes[(nodes.node_type == kind) & nodes.node.isin(real_nodes)]
        q = st.text_input("Search (MMSI, vessel or port name)", max_chars=64)
        if q:
            cand = cand[
                cand.node.str.contains(q, case=False)
                | cand.label.fillna("").str.contains(q, case=False)
            ]
        if cand.empty:
            st.info("No match.")
            st.stop()
        labels = (
            cand.node + "  " + cand.label.fillna("") + "  (degree " + cand.degree.astype(str) + ")"
        )
        focus = st.selectbox(
            "Entity (ranked by betweenness)",
            cand.node,
            format_func=lambda n: labels[cand.node == n].iloc[0],
        )
        rel = st.multiselect(
            "Relationship types",
            RELATIONSHIP_TYPES,
            default=["NEAR_PORT", "VOYAGE_ENDPOINT", "PART_OF_VOYAGE"],
            help="Add SHARED_PORT_PROXIMITY to see co-presence links (dense near busy ports).",
        )
        d0, d1 = datetime(2023, 1, 9).date(), datetime(2023, 1, 15).date()
        rng = st.date_input("Edge time window (UTC)", value=(d0, d1), min_value=d0, max_value=d1)
        if isinstance(rng, tuple) and len(rng) == 2:
            d0, d1 = rng
        radius = st.radio(
            "Neighbourhood", [1, 2], horizontal=True, format_func=lambda r: f"{r} hop"
        )
        max_nodes = st.slider("Max nodes shown", 10, 150, 50)
    s = pd.Timestamp(datetime.combine(d0, time.min), tz="UTC")
    e = pd.Timestamp(datetime.combine(d1, time.max), tz="UTC")
    H = filter_graph(G, set(rel) or None, s, e)
    if focus not in H:
        H.add_node(focus, **G.nodes[focus])
    E = ego_graph(H, focus, radius=radius, max_nodes=max_nodes)
    with c2:
        st.plotly_chart(ego_figure(E, focus), use_container_width=True)
        st.caption(
            f"{E.number_of_nodes()} nodes / {E.number_of_edges()} edges shown "
            f"(neighbours ranked by evidence count when capped). Hover an edge midpoint for evidence."
        )

    st.subheader("Edge evidence")
    edges = pd.DataFrame(
        [
            {
                "from": E.nodes[u].get("label"),
                "to": E.nodes[v].get("label"),
                "relationship": dd["relationship_type"],
                "evidence_table": dd["evidence_table"],
                "evidence_count": dd["evidence_count"],
                "start": dd.get("start_time"),
                "end": dd.get("end_time"),
                "quality": json.dumps(dd["quality"]),
                "evidence_ids": dd["evidence_ids"],
            }
            for u, v, dd in E.edges(data=True)
            if focus in (u, v)
        ]
    )
    if edges.empty:
        st.info("No edges for this entity under the current filters.")
    else:
        sel = st.dataframe(
            edges.drop(columns="evidence_ids"),
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            height=260,
        )
        rows = sel.selection.rows if sel and sel.selection else []
        if rows:
            r = edges.iloc[rows[0]]
            ids = [int(i) for i in r.evidence_ids]
            table = {
                "port_proximity_events": (
                    "SELECT e.proximity_id, e.mmsi, p.port_name, e.start_time, "
                    "e.end_time, e.n_obs, ROUND(e.min_distance_m::numeric) AS min_distance_m "
                    "FROM port_proximity_events e JOIN ports p USING (port_id) "
                    "WHERE e.proximity_id = ANY(:ids) ORDER BY e.start_time"
                ),
                "ais_positions": (
                    "SELECT position_id, mmsi, base_datetime, lat, lon, sog FROM ais_positions "
                    "WHERE position_id = ANY(:ids) ORDER BY base_datetime"
                ),
                "voyages": (
                    "SELECT voyage_id, mmsi, start_time, end_time, n_obs, distance_km FROM voyages "
                    "WHERE voyage_id = ANY(:ids)"
                ),
            }[r.evidence_table]
            st.markdown(
                f"**Supporting `{r.evidence_table}` rows** for {r.relationship} "
                f"({r.evidence_count} total, up to 25 stored on the edge)"
            )
            st.dataframe(
                pd.read_sql(text(table), get_engine(), params={"ids": ids}),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("Select an edge to load its supporting records from the database.")

    a, b = st.columns(2)
    with a:
        st.subheader("Centrality")
        nt = node_table()
        nt = nt[nt.node.isin(real_nodes) & nt.node_type.isin(["vessel", "port"])]
        st.dataframe(
            nt[["node_type", "label", "degree", "degree_centrality", "betweenness", "community"]]
            .head(30)
            .round(4),
            hide_index=True,
            use_container_width=True,
            height=360,
        )
        st.caption("Betweenness is sampled (500 seeded pivots) on the full heterogeneous graph.")
    with b:
        st.subheader("Frequent port-to-port transitions")
        st.dataframe(
            transitions()[["from_port", "to_port", "transitions", "distinct_vessels"]].head(30),
            hide_index=True,
            use_container_width=True,
            height=360,
        )
        st.caption("Consecutive distinct ports in each vessel's proximity sequence.")
    st.subheader("Repeated regional activity")
    rr = gm.repeated_region_activity(G, min_days=5)
    st.dataframe(
        rr.sort_values("n_obs", ascending=False).head(50),
        hide_index=True,
        use_container_width=True,
        height=220,
    )
    st.caption("Vessel x 0.1 deg grid region pairs observed on 5 or more distinct days.")

with tab_syn:
    st.markdown(
        "A **synthetic** scenario with a known answer: group A vessels visit ports 1-3, group B "
        "visits ports 4-6, and one bridge vessel visits ports 2 and 5. Expected: two "
        "communities joined only through the bridge, which has the highest vessel betweenness."
    )
    ev, ports, expected = make_network_scenario()
    mm = sorted(ev.mmsi.unique())
    Gs = build_graph(
        pd.DataFrame(
            {"mmsi": mm, "vessel_name": [f"SYN {m[-5:]}" for m in mm], "is_synthetic": True}
        ),
        ports,
        ev,
        source_dataset="SYNTHETIC scenario",
    )
    comms = gm.communities(Gs)
    sub = gm.vessel_port_subgraph(Gs)
    st.plotly_chart(
        ego_figure(sub, f"vessel:{expected['bridge']}", height=480), use_container_width=True
    )
    t = gm.node_table(sub, betweenness_k=None)
    top = t[t.node_type == "vessel"].iloc[0]
    ok = top.node == f"vessel:{expected['bridge']}" and len(comms) == expected["n_communities"]
    st.markdown(
        f"Communities found: **{len(comms)}** (expected {expected['n_communities']}); top-betweenness "
        f"vessel: **{top.label}** (expected SYN {expected['bridge'][-5:]}). "
        f"{'Recovered as expected.' if ok else 'Did NOT match expectation.'}"
    )
