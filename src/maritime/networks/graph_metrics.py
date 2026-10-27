"""Graph metrics for the vessel/port/region network."""

from __future__ import annotations

from collections import Counter
from typing import Any

import networkx as nx
import pandas as pd


def node_table(G: nx.Graph, betweenness_k: int | None = 500, seed: int = 0) -> pd.DataFrame:
    """Degree and betweenness centrality for every node.

    Betweenness is exact for small graphs and sampled (k pivots, fixed seed) for larger
    ones, so results are reproducible.
    """
    if G.number_of_nodes() == 0:
        return pd.DataFrame(
            columns=["node", "node_type", "label", "degree", "degree_centrality", "betweenness"]
        )
    k = None if betweenness_k is None or G.number_of_nodes() <= betweenness_k else betweenness_k
    btw = nx.betweenness_centrality(G, k=k, seed=seed, normalized=True)
    deg = nx.degree_centrality(G)
    rows = [
        {
            "node": n,
            "node_type": d.get("node_type"),
            "label": d.get("label"),
            "degree": G.degree(n),
            "degree_centrality": deg[n],
            "betweenness": btw[n],
        }
        for n, d in G.nodes(data=True)
    ]
    return pd.DataFrame(rows).sort_values("betweenness", ascending=False).reset_index(drop=True)


def components(G: nx.Graph) -> list[set[str]]:
    return sorted(nx.connected_components(G), key=len, reverse=True)


def vessel_port_subgraph(G: nx.Graph) -> nx.Graph:
    """Vessels + ports with NEAR_PORT edges only (regions and voyages excluded).

    Region nodes connect almost everything and would swamp community structure.
    """
    edges = [(u, v) for u, v, d in G.edges(data=True) if d["relationship_type"] == "NEAR_PORT"]
    return G.edge_subgraph(edges).copy()


def communities(G: nx.Graph, seed: int = 0) -> list[set[str]]:
    """Louvain communities on the vessel-port subgraph (weighted by evidence count)."""
    H = vessel_port_subgraph(G)
    if H.number_of_edges() == 0:
        return []
    for _, _, d in H.edges(data=True):
        d["weight"] = d.get("evidence_count", 1)
    comms = nx.community.louvain_communities(H, weight="weight", seed=seed)
    return sorted(comms, key=len, reverse=True)


def port_transitions(proximity: pd.DataFrame) -> pd.DataFrame:
    """Frequent route patterns: counts of consecutive distinct ports per vessel (A -> B)."""
    counts: Counter = Counter()
    vessels: dict[tuple[int, int], set] = {}
    for mmsi, g in proximity.sort_values("start_time").groupby("mmsi"):
        seq = g["port_id"].tolist()
        dedup = [p for i, p in enumerate(seq) if i == 0 or p != seq[i - 1]]
        for a, b in zip(dedup[:-1], dedup[1:], strict=True):
            counts[(a, b)] += 1
            vessels.setdefault((a, b), set()).add(mmsi)
    rows = [
        {
            "from_port_id": int(a),
            "to_port_id": int(b),
            "transitions": n,
            "distinct_vessels": len(vessels[(a, b)]),
        }
        for (a, b), n in counts.items()
    ]
    return (
        pd.DataFrame(rows).sort_values("transitions", ascending=False).reset_index(drop=True)
        if rows
        else pd.DataFrame(columns=["from_port_id", "to_port_id", "transitions", "distinct_vessels"])
    )


def repeated_region_activity(G: nx.Graph, min_days: int = 3) -> pd.DataFrame:
    rows = [
        {
            "vessel": u if u.startswith("vessel:") else v,
            "region": v if v.startswith("region:") else u,
            "n_days": d["quality"]["n_days"],
            "n_obs": d["n_observations"],
        }
        for u, v, d in G.edges(data=True)
        if d["relationship_type"] == "IN_REGION" and d["quality"]["n_days"] >= min_days
    ]
    return pd.DataFrame(rows)


def summary(G: nx.Graph) -> dict[str, Any]:
    types = Counter(d.get("node_type") for _, d in G.nodes(data=True))
    rels = Counter(d["relationship_type"] for _, _, d in G.edges(data=True))
    comps = components(G)
    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "node_types": dict(types),
        "relationship_types": dict(rels),
        "connected_components": len(comps),
        "largest_component_size": len(comps[0]) if comps else 0,
        "density": round(nx.density(G), 6) if G.number_of_nodes() > 1 else 0.0,
    }
