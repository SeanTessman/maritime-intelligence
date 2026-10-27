"""Plotly rendering of a focused (ego) subgraph. Never render the full graph."""

from __future__ import annotations

import networkx as nx
import plotly.graph_objects as go

NODE_COLORS = {"vessel": "#5aa9e6", "port": "#f4a259", "region": "#8d99ae", "voyage": "#9b5de5"}
EDGE_COLORS = {
    "NEAR_PORT": "#f4a259",
    "IN_REGION": "#5c677d",
    "PART_OF_VOYAGE": "#9b5de5",
    "VOYAGE_ENDPOINT": "#c77dff",
    "SHARED_PORT_PROXIMITY": "#2ec4b6",
}


def ego_figure(
    H: nx.Graph, focus: str | None = None, seed: int = 7, height: int = 620
) -> go.Figure:
    """Spring-layout figure; edges grouped per relationship type so the legend filters them."""
    pos = nx.spring_layout(H, seed=seed, k=1.2 / max(H.number_of_nodes() ** 0.5, 1))
    fig = go.Figure()
    for rel, color in EDGE_COLORS.items():
        xs, ys, mx, my, hover = [], [], [], [], []
        for u, v, d in H.edges(data=True):
            if d["relationship_type"] != rel:
                continue
            xs += [pos[u][0], pos[v][0], None]
            ys += [pos[u][1], pos[v][1], None]
            mx.append((pos[u][0] + pos[v][0]) / 2)
            my.append((pos[u][1] + pos[v][1]) / 2)
            hover.append(
                f"{rel}<br>{H.nodes[u].get('label')} - {H.nodes[v].get('label')}"
                f"<br>evidence: {d['evidence_count']} {d['evidence_table']} rows"
                f"<br>{(d.get('start_time') or '')[:16]} -> {(d.get('end_time') or '')[:16]}"
            )
        if not xs:
            continue
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line={"width": 1.2, "color": color},
                hoverinfo="skip",
                name=rel,
                legendgroup=rel,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=mx,
                y=my,
                mode="markers",
                marker={"size": 6, "color": color, "opacity": 0.01},
                hovertext=hover,
                hoverinfo="text",
                showlegend=False,
                legendgroup=rel,
            )
        )
    for ntype, color in NODE_COLORS.items():
        nodes = [n for n, d in H.nodes(data=True) if d.get("node_type") == ntype]
        if not nodes:
            continue
        fig.add_trace(
            go.Scatter(
                x=[pos[n][0] for n in nodes],
                y=[pos[n][1] for n in nodes],
                mode="markers+text",
                marker={
                    "size": [20 if n == focus else 11 for n in nodes],
                    "color": color,
                    "line": {
                        "width": [3 if n == focus else 0.5 for n in nodes],
                        "color": "#ffffff",
                    },
                },
                text=[
                    str(H.nodes[n].get("label"))[:18] if ntype != "voyage" else "" for n in nodes
                ],
                textposition="top center",
                textfont={"size": 10},
                hovertext=[
                    f"{ntype}: {H.nodes[n].get('label')}<br>degree {H.degree(n)}" for n in nodes
                ],
                hoverinfo="text",
                name=ntype,
            )
        )
    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis={"visible": False},
        yaxis={"visible": False},
        legend={"orientation": "h", "y": -0.02},
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig
