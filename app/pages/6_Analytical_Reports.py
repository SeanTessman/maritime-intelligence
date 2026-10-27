"""Analytical Reports: generate reproducible reports with sources, methods and limitations."""

from __future__ import annotations

import streamlit as st
from components.common import page_setup

from maritime import queries
from maritime.investigations import cases, reporting

page_setup("Analytical Reports")
st.title("Analytical Reports")
st.caption(
    "Every report includes the analysis period, data sources, methods, observations, evidence "
    "references, limitations, the disclaimer and a generation timestamp."
)

kind = st.selectbox(
    "Report",
    [
        "Data-quality report",
        "Model evaluation summary",
        "Network analysis summary",
        "Vessel activity report",
        "Anomaly investigation report (case)",
    ],
)
rep = None
try:
    if kind == "Data-quality report":
        rep = reporting.data_quality_report()
    elif kind == "Model evaluation summary":
        rep = reporting.model_evaluation_report()
    elif kind == "Network analysis summary":
        rep = reporting.network_report()
    elif kind == "Vessel activity report":
        q = st.text_input("Vessel MMSI or name", max_chars=64)
        vs, _ = queries.search_vessels(q or None, include_synthetic=True, limit=100)
        if len(vs):
            m = st.selectbox(
                "Vessel",
                vs.mmsi,
                format_func=lambda x: f"{x} {vs.set_index('mmsi').vessel_name[x] or ''}",
            )
            rep = reporting.vessel_activity_report(m)
    else:
        cl = cases.list_cases()
        if cl.empty:
            st.info("No cases yet.")
        else:
            cid = st.selectbox(
                "Case", cl.case_id, format_func=lambda i: f"{i}: {cl.set_index('case_id').title[i]}"
            )
            rep = reporting.case_report(int(cid))
except FileNotFoundError as exc:
    st.warning(
        f"Missing pipeline output: {exc}. Run the pipeline first (`python tasks.py pipeline`)."
    )

if rep is not None:
    stem = rep.report_type
    c = st.columns(4)
    c[0].download_button("Download HTML", rep.to_html().encode(), f"{stem}.html", "text/html")
    c[1].download_button(
        "Download Markdown", rep.to_markdown().encode(), f"{stem}.md", "text/markdown"
    )
    c[2].download_button("Download CSV", rep.to_csv(), f"{stem}.csv", "text/csv")
    c[3].download_button("Download PDF", rep.to_pdf(), f"{stem}.pdf", "application/pdf")
    st.divider()
    st.markdown(rep.to_markdown())
