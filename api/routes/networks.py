"""Network endpoints (focused neighbourhoods only, never the full graph)."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from api.deps import MMSIPath
from api.schemas import NetworkEdge, NetworkNode, VesselNetwork
from maritime.networks.graph_builder import RELATIONSHIP_TYPES, ego_graph, filter_graph, vessel_node
from maritime.networks.pipeline import load_network, network_path

router = APIRouter(prefix="/networks", tags=["networks"])


@lru_cache(maxsize=1)
def _graph(mtime: float):  # noqa: ANN202 - cache keyed on file mtime
    return load_network()


@router.get("/vessels/{mmsi}", response_model=VesselNetwork)
def vessel_network(
    mmsi: MMSIPath,
    relationship_type: list[str] | None = Query(None),
    max_nodes: int = Query(100, ge=2, le=300),
) -> VesselNetwork:
    path = network_path()
    if not path.exists():
        raise HTTPException(503, "network not built; run `python tasks.py network`")
    if relationship_type and not set(relationship_type) <= set(RELATIONSHIP_TYPES):
        raise HTTPException(422, f"relationship_type must be in {RELATIONSHIP_TYPES}")
    G = _graph(path.stat().st_mtime)
    node = vessel_node(mmsi)
    if node not in G:
        raise HTTPException(404, f"vessel {mmsi} not in network")
    H = filter_graph(G, set(relationship_type) if relationship_type else None)
    if node not in H:
        H.add_node(node, **G.nodes[node])
    E = ego_graph(H, node, radius=1, max_nodes=max_nodes)
    nodes = [
        NetworkNode(id=n, node_type=d.get("node_type", "?"), label=str(d.get("label")))
        for n, d in E.nodes(data=True)
    ]
    edges = [
        NetworkEdge(
            source=u,
            target=v,
            **{k: d[k] for k in NetworkEdge.model_fields if k not in ("source", "target")},
        )
        for u, v, d in E.edges(data=True)
    ]
    return VesselNetwork(
        center=node,
        nodes=nodes,
        edges=edges,
        note="Edges are evidence-backed co-observations. Proximity is not docking, "
        "and shared proximity is not evidence of interaction.",
    )
