"""Network: node/edge creation, evidence on every edge, centrality on known graphs."""

from __future__ import annotations

import networkx as nx
import pandas as pd
import pytest

from maritime.networks import graph_metrics as gm
from maritime.networks.graph_builder import (
    Evidence,
    add_evidence_edge,
    build_graph,
    ego_graph,
    filter_graph,
    port_node,
    region_activity_from_steps,
    vessel_node,
)
from maritime.synthetic import make_network_scenario


@pytest.fixture(scope="module")
def scenario():
    events, ports, expected = make_network_scenario()
    mmsis = sorted(events["mmsi"].unique())
    vessels = pd.DataFrame({"mmsi": mmsis, "vessel_name": mmsis, "is_synthetic": True})
    G = build_graph(vessels, ports, events, source_dataset="SYNTHETIC scenario")
    return G, events, ports, expected


def test_nodes_created_for_every_entity(scenario):
    G, _, _, exp = scenario
    s = gm.summary(G)
    assert s["node_types"] == {"vessel": exp["n_vessels"], "port": exp["n_ports"]}


def test_edges_match_known_visits(scenario):
    G, events, _, exp = scenario
    near = [(u, v) for u, v, d in G.edges(data=True) if d["relationship_type"] == "NEAR_PORT"]
    assert len(near) == len(events.groupby(["mmsi", "port_id"]))
    assert G.has_edge(vessel_node(exp["bridge"]), port_node(2))
    assert G.has_edge(vessel_node(exp["bridge"]), port_node(5))
    assert not G.has_edge(vessel_node(exp["group_a"][0]), port_node(5))


def test_every_edge_has_evidence_and_provenance(scenario):
    G, events, _, _ = scenario
    valid_ids = set(events["proximity_id"])
    for _, _, d in G.edges(data=True):
        assert d["evidence_ids"] and set(d["evidence_ids"]) <= valid_ids
        assert d["source_dataset"] == "SYNTHETIC scenario"
        assert d["start_time"] and d["end_time"] and d["quality"]
        assert d["relationship_type"] in {"NEAR_PORT", "SHARED_PORT_PROXIMITY"}


def test_edge_without_evidence_is_refused():
    G = nx.Graph()
    G.add_nodes_from(["vessel:1", "port:1"])
    with pytest.raises(ValueError, match="without evidence"):
        add_evidence_edge(G, "vessel:1", "port:1", Evidence("NEAR_PORT", "x", "t", []))
    with pytest.raises(KeyError):
        add_evidence_edge(G, "vessel:1", "port:9", Evidence("NEAR_PORT", "x", "t", [1]))


def test_two_communities_and_bridge_has_top_vessel_betweenness(scenario):
    G, _, _, exp = scenario
    comms = gm.communities(G, seed=0)
    assert len(comms) == exp["n_communities"]
    a = {vessel_node(m) for m in exp["group_a"]}
    b = {vessel_node(m) for m in exp["group_b"]}
    assert any(a <= c for c in comms) and any(b <= c for c in comms)
    nodes = gm.node_table(gm.vessel_port_subgraph(G))
    top_vessel = nodes[nodes.node_type == "vessel"].iloc[0]["node"]
    assert top_vessel == vessel_node(exp["bridge"])


def test_centrality_on_known_star_and_path_graphs():
    star = nx.star_graph(4)  # centre 0 with 4 leaves
    t = gm.node_table(star, betweenness_k=None).set_index("node")
    assert t.loc[0, "degree_centrality"] == 1.0 and t.loc[0, "betweenness"] == 1.0
    assert t.loc[1, "betweenness"] == 0.0
    path = gm.node_table(nx.path_graph(3), betweenness_k=None).set_index("node")
    assert (
        path.loc[1, "betweenness"] == 1.0
    )  # middle of a 3-node path lies on the only shortest path


def test_shared_proximity_requires_overlap(scenario):
    G, _, _, exp = scenario
    # Group A vessels visited the same ports at random times within 48 h; any shared edge
    # must connect vessels of the same group, never A to B (they share no port).
    for u, v, d in G.edges(data=True):
        if d["relationship_type"] == "SHARED_PORT_PROXIMITY":
            assert "NOT evidence of interaction" in d["quality"]["note"]
            ga = {vessel_node(m) for m in exp["group_a"]}
            assert (u in ga) == (v in ga) or exp["bridge"] in (u + v)


def test_filters_and_ego(scenario):
    G, _, _, exp = scenario
    only_port = filter_graph(G, {"NEAR_PORT"})
    assert {d["relationship_type"] for _, _, d in only_port.edges(data=True)} == {"NEAR_PORT"}
    late = filter_graph(G, start=pd.Timestamp("2031-01-01", tz="UTC"))
    assert late.number_of_edges() == 0
    ego = ego_graph(only_port, vessel_node(exp["bridge"]))
    assert set(ego.nodes) == {vessel_node(exp["bridge"]), port_node(2), port_node(5)}


def test_port_transitions_counts():
    ev = pd.DataFrame(
        {
            "mmsi": ["a", "a", "a", "b", "b"],
            "port_id": [1, 1, 2, 1, 2],
            "start_time": pd.to_datetime(
                ["2030-01-01", "2030-01-02", "2030-01-03", "2030-01-01", "2030-01-02"], utc=True
            ),
        }
    )
    t = gm.port_transitions(ev)
    assert t.iloc[0].to_dict() == {
        "from_port_id": 1,
        "to_port_id": 2,
        "transitions": 2,
        "distinct_vessels": 2,
    }


def test_region_activity_from_steps():
    steps = pd.DataFrame(
        {
            "mmsi": ["a"] * 3,
            "lat": [40.61, 40.62, 40.75],
            "lon": [-74.05] * 3,
            "base_datetime": pd.to_datetime(
                ["2030-01-01T00:00", "2030-01-02T00:00", "2030-01-02T01:00"], utc=True
            ),
            "position_id": [1, 2, 3],
        }
    )
    r = region_activity_from_steps(steps).set_index("region")
    assert r.loc["G40.6_-74.1", "n_obs"] == 2 and r.loc["G40.6_-74.1", "n_days"] == 2
    assert r.loc["G40.7_-74.1", "position_ids"] == [3]
