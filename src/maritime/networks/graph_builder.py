"""Heterogeneous, evidence-backed relationship graph (NetworkX).

Nodes:  vessel:<mmsi>  port:<port_id>  region:<grid cell>  voyage:<voyage_id>
Edges (one per node pair, evidence aggregated):
  NEAR_PORT              vessel - port     observed within the proximity radius of a WPI point
  IN_REGION              vessel - region   observed in a 0.1 deg grid region
  PART_OF_VOYAGE         vessel - voyage   trajectory segment belongs to the vessel
  VOYAGE_ENDPOINT        voyage - port     segment started/ended within the radius of a port
  SHARED_PORT_PROXIMITY  vessel - vessel   both near the same port with overlapping times

Every edge carries: relationship_type, evidence ids (capped), time interval, source
dataset and evidence-quality metadata. `add_evidence_edge` refuses to create an edge
without evidence. Proximity is not docking, and SHARED_PORT_PROXIMITY does NOT mean
the vessels interacted, only that they were near the same reference point at once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import pandas as pd

MAX_EVIDENCE_IDS = 25
RELATIONSHIP_TYPES = (
    "NEAR_PORT",
    "IN_REGION",
    "PART_OF_VOYAGE",
    "VOYAGE_ENDPOINT",
    "SHARED_PORT_PROXIMITY",
)


@dataclass
class Evidence:
    """Supporting records for one relationship."""

    relationship_type: str
    source_dataset: str
    evidence_table: str
    evidence_ids: list[int] = field(default_factory=list)
    start_time: pd.Timestamp | None = None
    end_time: pd.Timestamp | None = None
    n_observations: int = 0
    quality: dict[str, Any] = field(default_factory=dict)

    def as_attrs(self) -> dict[str, Any]:
        return {
            "relationship_type": self.relationship_type,
            "source_dataset": self.source_dataset,
            "evidence_table": self.evidence_table,
            "evidence_ids": [int(x) for x in self.evidence_ids[:MAX_EVIDENCE_IDS]],
            "evidence_count": len(self.evidence_ids),
            "start_time": None
            if self.start_time is None
            else pd.Timestamp(self.start_time).isoformat(),
            "end_time": None if self.end_time is None else pd.Timestamp(self.end_time).isoformat(),
            "n_observations": int(self.n_observations),
            "quality": self.quality,
        }


def vessel_node(mmsi: str) -> str:
    return f"vessel:{mmsi}"


def port_node(port_id: int) -> str:
    return f"port:{int(port_id)}"


def add_evidence_edge(G: nx.Graph, u: str, v: str, ev: Evidence) -> None:
    if not ev.evidence_ids:
        raise ValueError(f"refusing to add {ev.relationship_type} edge {u}-{v} without evidence")
    if ev.relationship_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"unknown relationship type {ev.relationship_type}")
    for n in (u, v):
        if n not in G:
            raise KeyError(f"node {n} must be added before its edges")
    G.add_edge(u, v, **ev.as_attrs())


def _interval(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.to_datetime(df["start_time"]).min(), pd.to_datetime(df["end_time"]).max()


def build_graph(
    vessels: pd.DataFrame,
    ports: pd.DataFrame,
    proximity: pd.DataFrame,
    region_activity: pd.DataFrame | None = None,
    voyages: pd.DataFrame | None = None,
    source_dataset: str = "NOAA AIS + NGA WPI",
    min_shared_overlap_s: float = 0.0,
) -> nx.Graph:
    """Build the graph from tabular inputs (DB extracts or synthetic frames).

    vessels:   mmsi, vessel_name?, vessel_type_group?, is_synthetic?
    ports:     port_id, port_name, lat, lon
    proximity: proximity_id, mmsi, port_id, start_time, end_time, n_obs, min_distance_m, radius_m
    region_activity: mmsi, region, n_obs, start_time, end_time, n_days, position_ids
    voyages:   voyage_id, mmsi, start_time, end_time, n_obs, distance_km, start_port_id, end_port_id
    """
    G = nx.Graph()
    for v in vessels.to_dict("records"):
        G.add_node(
            vessel_node(v["mmsi"]),
            node_type="vessel",
            label=v.get("vessel_name") or v["mmsi"],
            mmsi=v["mmsi"],
            vessel_type_group=v.get("vessel_type_group", "other"),
            is_synthetic=bool(v.get("is_synthetic", False)),
        )
    for p in ports.to_dict("records"):
        G.add_node(
            port_node(p["port_id"]),
            node_type="port",
            label=p["port_name"],
            port_id=int(p["port_id"]),
            lat=float(p["lat"]),
            lon=float(p["lon"]),
        )

    # NEAR_PORT
    for (mmsi, pid), g in proximity.groupby(["mmsi", "port_id"]):
        if vessel_node(mmsi) not in G or port_node(pid) not in G:
            continue
        s, e = _interval(g)
        add_evidence_edge(
            G,
            vessel_node(mmsi),
            port_node(pid),
            Evidence(
                "NEAR_PORT",
                source_dataset,
                "port_proximity_events",
                g["proximity_id"].astype(int).tolist(),
                s,
                e,
                int(g["n_obs"].sum()),
                {
                    "n_events": len(g),
                    "min_distance_m": round(float(g["min_distance_m"].min()), 1),
                    "radius_m": float(g["radius_m"].iloc[0]),
                    "note": "geographic proximity to a WPI reference point; not verified docking",
                },
            ),
        )

    # SHARED_PORT_PROXIMITY via an interval sweep per port
    pair_ev: dict[tuple[str, str], list] = defaultdict(list)
    for pid, g in proximity.groupby("port_id"):
        g = g.sort_values("start_time")
        active: list[dict] = []
        for r in g.to_dict("records"):
            active = [a for a in active if a["end_time"] >= r["start_time"]]
            for a in active:
                if a["mmsi"] == r["mmsi"]:
                    continue
                overlap = (min(a["end_time"], r["end_time"]) - r["start_time"]).total_seconds()
                if overlap < min_shared_overlap_s:
                    continue
                key = tuple(sorted((a["mmsi"], r["mmsi"])))
                pair_ev[key].append(
                    (
                        pid,
                        a["proximity_id"],
                        r["proximity_id"],
                        r["start_time"],
                        min(a["end_time"], r["end_time"]),
                        overlap,
                    )
                )
            active.append(r)
    for (m1, m2), items in pair_ev.items():
        if vessel_node(m1) not in G or vessel_node(m2) not in G:
            continue
        ids = sorted({i for it in items for i in (it[1], it[2])})
        add_evidence_edge(
            G,
            vessel_node(m1),
            vessel_node(m2),
            Evidence(
                "SHARED_PORT_PROXIMITY",
                source_dataset,
                "port_proximity_events",
                ids,
                min(it[3] for it in items),
                max(it[4] for it in items),
                len(items),
                {
                    "n_cooccurrences": len(items),
                    "ports": sorted({int(it[0]) for it in items}),
                    "total_overlap_h": round(sum(it[5] for it in items) / 3600, 2),
                    "note": "co-presence near the same port reference point; NOT evidence of interaction",
                },
            ),
        )

    # IN_REGION
    if region_activity is not None and not region_activity.empty:
        for r in region_activity.to_dict("records"):
            reg = f"region:{r['region']}"
            if reg not in G:
                G.add_node(reg, node_type="region", label=r["region"])
            if vessel_node(r["mmsi"]) not in G:
                continue
            add_evidence_edge(
                G,
                vessel_node(r["mmsi"]),
                reg,
                Evidence(
                    "IN_REGION",
                    source_dataset,
                    "ais_positions",
                    list(r["position_ids"]),
                    r["start_time"],
                    r["end_time"],
                    int(r["n_obs"]),
                    {"n_days": int(r["n_days"]), "note": "position ids are a sample of the fixes"},
                ),
            )

    # PART_OF_VOYAGE + VOYAGE_ENDPOINT (only voyages that touch a port, to keep the graph small)
    if voyages is not None and not voyages.empty:
        vv = voyages[voyages["start_port_id"].notna() | voyages["end_port_id"].notna()]
        for r in vv.to_dict("records"):
            vn = f"voyage:{int(r['voyage_id'])}"
            if vessel_node(r["mmsi"]) not in G:
                continue
            G.add_node(
                vn,
                node_type="voyage",
                label=f"voyage {int(r['voyage_id'])}",
                mmsi=r["mmsi"],
                distance_km=float(r["distance_km"] or 0),
            )
            ev_kw = dict(
                source_dataset=source_dataset,
                evidence_table="voyages",
                evidence_ids=[int(r["voyage_id"])],
                start_time=r["start_time"],
                end_time=r["end_time"],
                n_observations=int(r["n_obs"]),
            )
            add_evidence_edge(
                G,
                vessel_node(r["mmsi"]),
                vn,
                Evidence(
                    "PART_OF_VOYAGE",
                    quality={
                        "distance_km": round(float(r["distance_km"] or 0), 2),
                        "note": "inferred trajectory segment (gap/jump split)",
                    },
                    **ev_kw,
                ),
            )
            for end in ("start_port_id", "end_port_id"):
                if pd.notna(r[end]) and port_node(r[end]) in G:
                    add_evidence_edge(
                        G,
                        vn,
                        port_node(r[end]),
                        Evidence(
                            "VOYAGE_ENDPOINT", quality={"endpoint": end.split("_")[0]}, **ev_kw
                        ),
                    )
    return G


def region_activity_from_steps(steps: pd.DataFrame, sample: int = 20) -> pd.DataFrame:
    """Aggregate fixes into vessel x grid-region activity rows with sampled evidence ids."""
    from maritime.processing.spatial_features import grid_cell

    df = steps[["mmsi", "base_datetime", "lat", "lon", "position_id"]].copy()
    df["region"] = grid_cell(df["lat"], df["lon"]).to_numpy()
    df["day"] = df["base_datetime"].dt.date

    def _agg(g: pd.DataFrame) -> pd.Series:
        idx = (
            g.index
            if len(g) <= sample
            else g.index[pd.Series(range(len(g))).sample(sample, random_state=0).sort_values()]
        )
        return pd.Series(
            {
                "n_obs": len(g),
                "start_time": g["base_datetime"].min(),
                "end_time": g["base_datetime"].max(),
                "n_days": g["day"].nunique(),
                "position_ids": g.loc[idx, "position_id"].astype(int).tolist(),
            }
        )

    return df.groupby(["mmsi", "region"]).apply(_agg, include_groups=False).reset_index()


def filter_graph(
    G: nx.Graph,
    relationship_types: set[str] | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> nx.Graph:
    """Subgraph view keeping edges of the given types that overlap [start, end]."""
    keep = []
    for u, v, d in G.edges(data=True):
        if relationship_types and d["relationship_type"] not in relationship_types:
            continue
        if start is not None and d.get("end_time") and pd.Timestamp(d["end_time"]) < start:
            continue
        if end is not None and d.get("start_time") and pd.Timestamp(d["start_time"]) > end:
            continue
        keep.append((u, v))
    H = G.edge_subgraph(keep).copy()
    return H


def ego_graph(G: nx.Graph, node: str, radius: int = 1, max_nodes: int = 150) -> nx.Graph:
    """Focused neighbourhood; if too large, keep the strongest-evidence neighbours."""
    H = nx.ego_graph(G, node, radius=radius)
    if H.number_of_nodes() <= max_nodes:
        return H
    nbrs = sorted(G[node].items(), key=lambda kv: kv[1].get("evidence_count", 0), reverse=True)
    keep = {node, *[n for n, _ in nbrs[: max_nodes - 1]]}
    return G.subgraph(keep).copy()
