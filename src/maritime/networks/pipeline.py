"""Build the network from the database and persist it for the dashboard and API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd
from sqlalchemy import Engine, text

from maritime.config import get_settings
from maritime.database import get_engine
from maritime.networks import graph_metrics as gm
from maritime.networks.graph_builder import build_graph, region_activity_from_steps


def network_path() -> Path:
    return get_settings().processed_dir / "network.json"


def extract_inputs(engine: Engine) -> dict[str, pd.DataFrame]:
    q = {
        # Real (NOAA) vessels only; the synthetic network scenario is built separately.
        "vessels": "SELECT mmsi, vessel_name, vessel_type, is_synthetic FROM vessels "
        "WHERE NOT is_synthetic",
        "ports": "SELECT port_id, port_name, lat, lon FROM ports",
        "proximity": "SELECT e.proximity_id, e.mmsi, e.port_id, e.start_time, e.end_time, e.n_obs, "
        "e.min_distance_m, e.radius_m FROM port_proximity_events e "
        "JOIN vessels v USING (mmsi) WHERE NOT v.is_synthetic",
        "voyages": "SELECT o.voyage_id, o.mmsi, o.start_time, o.end_time, o.n_obs, o.distance_km, "
        "o.start_port_id, o.end_port_id FROM voyages o JOIN vessels v USING (mmsi) "
        "WHERE NOT v.is_synthetic",
    }
    out = {k: pd.read_sql(text(v), engine) for k, v in q.items()}
    from maritime.processing.vessel_features import vessel_type_group

    out["vessels"]["vessel_type_group"] = out["vessels"]["vessel_type"].map(vessel_type_group)
    for k in ("proximity", "voyages"):
        for c in ("start_time", "end_time"):
            out[k][c] = pd.to_datetime(out[k][c], utc=True)
    return out


def build_and_save_network(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or get_engine()
    inp = extract_inputs(engine)
    steps = pd.read_parquet(
        get_settings().processed_dir / "steps.parquet",
        columns=["mmsi", "base_datetime", "lat", "lon", "position_id"],
    )
    steps = steps[steps["mmsi"].isin(set(inp["vessels"]["mmsi"]))]
    regions = region_activity_from_steps(steps)
    from maritime.processing.pipeline import load_thresholds

    ncfg = load_thresholds()["network"]
    G = build_graph(
        inp["vessels"],
        inp["ports"],
        inp["proximity"],
        regions,
        inp["voyages"],
        min_shared_overlap_s=float(ncfg["min_shared_overlap_s"]),
    )
    nodes = gm.node_table(G, betweenness_k=int(ncfg["betweenness_k"]))
    comms = gm.communities(G)
    comm_of = {n: i for i, c in enumerate(comms) for n in c}
    nodes["community"] = nodes["node"].map(comm_of)
    trans = gm.port_transitions(inp["proximity"])
    port_names = inp["ports"].set_index("port_id")["port_name"]
    trans["from_port"] = trans["from_port_id"].map(port_names)
    trans["to_port"] = trans["to_port_id"].map(port_names)
    summ = gm.summary(G) | {"communities_vessel_port": len(comms)}

    out = get_settings().processed_dir
    network_path().write_text(json.dumps(nx.node_link_data(G, edges="links"), default=str))
    nodes.to_parquet(out / "network_nodes.parquet", index=False)
    trans.to_parquet(out / "port_transitions.parquet", index=False)
    (out / "network_summary.json").write_text(json.dumps(summ, indent=2))
    return summ


def load_network(path: Path | None = None) -> nx.Graph:
    data = json.loads((path or network_path()).read_text())
    return nx.node_link_graph(data, edges="links")
