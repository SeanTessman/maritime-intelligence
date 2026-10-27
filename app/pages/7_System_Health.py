"""System & Data Health: connectivity, ingestion history, counts, missingness, model, provenance."""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from components.common import SERIES, db_status, page_setup

from maritime import queries
from maritime.config import get_settings
from maritime.database import read_sql

page_setup("System & Data Health", need_db=False)
st.title("System & Data Health")

ok, msg = db_status()
(st.success if ok else st.error)(f"Database: {msg}")
if not ok:
    st.markdown(
        "**How to fix:** `python tasks.py db-up` (Docker) or start your local PostgreSQL, then "
        "`python tasks.py migrate`. Check POSTGRES_* values in `.env`."
    )
    st.stop()

runs = queries.ingestion_runs()
done = runs[runs.status == "completed"]
m = st.columns(4)
m[0].metric("Ingestion runs", len(runs))
m[1].metric("Failed / running", int((runs.status != "completed").sum()))
last = done.finished_at.max() if len(done) else None
m[2].metric("Last successful run (UTC)", f"{last:%Y-%m-%d}" if last is not None else "never")
mr = queries.model_runs()
m[3].metric("Model runs recorded", len(mr))

st.subheader("Ingestion history")
st.dataframe(runs, hide_index=True, use_container_width=True)

st.subheader("Record counts")
st.dataframe(queries.table_counts(), hide_index=True, use_container_width=True)

st.subheader("Missing data (rows inside the study region)")
miss, flags = {}, {}
for qr in read_sql(
    "SELECT quality_report FROM ingestion_runs WHERE status = 'completed'"
).quality_report:
    for k, v in (qr.get("missing_by_field") or {}).items():
        miss[k] = miss.get(k, 0) + v
    for k, v in (qr.get("quality_flags") or {}).items():
        flags[k] = flags.get(k, 0) + v
tot = max(int(done.rows_in_bbox.sum()), 1)
mdf = pd.DataFrame(
    {"field": list(miss), "pct": [100 * v / tot for v in miss.values()]}
).sort_values("pct")
fig = go.Figure(
    go.Bar(
        x=mdf.pct,
        y=mdf.field,
        orientation="h",
        marker={"color": SERIES[0], "cornerradius": 4},
        hovertemplate="%{y}: %{x:.2f}% missing<extra></extra>",
    )
)
fig.update_layout(height=340, xaxis_title="% of study-region rows missing", xaxis_range=[0, 100])
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Missing = empty in the NOAA file. Sentinel 'not available' codes (SOG 102.3, COG 360, "
    "heading 511) are counted as quality flags below, not as missing."
)
st.dataframe(pd.DataFrame({"flag": list(flags), "rows": list(flags.values())}), hide_index=True)

st.subheader("Model")
if len(mr):
    r = mr.iloc[0]
    st.markdown(
        f"**{r.model_name}** `{r.model_version}` - trained on {r.n_train} tracks "
        f"({r.train_start:%Y-%m-%d} to {r.train_end:%Y-%m-%d}), evaluated on {r.n_eval} "
        f"later tracks; artifact `{r.artifact_path}`; data hash `{r.data_hash}`."
    )
    st.json(r.metrics if isinstance(r.metrics, dict) else json.loads(r.metrics), expanded=False)

st.subheader("Dataset provenance")
st.dataframe(queries.data_sources(), hide_index=True, use_container_width=True)
proc = get_settings().processed_dir
st.markdown("**Processed artifacts**")
st.dataframe(
    pd.DataFrame(
        [
            {
                "file": p.name,
                "MB": round(p.stat().st_size / 1e6, 2),
                "modified": pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC"),
            }
            for p in sorted(proc.glob("*"))
            if p.is_file() and p.name != ".gitkeep"
        ]
    ),
    hide_index=True,
)
