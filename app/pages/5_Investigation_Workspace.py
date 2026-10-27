"""Investigation Workspace: open a case, attach detections/observations, take notes, set status."""

from __future__ import annotations

import json

import streamlit as st
from components.common import page_setup

from maritime.investigations import cases, evidence, reporting

page_setup("Investigation Workspace")
st.title("Investigation Workspace")
st.caption(
    "Cases are stored locally in PostgreSQL. Statuses are neutral by design: "
    + ", ".join(cases.CASE_STATUSES)
    + "."
)

cl = cases.list_cases()
c1, c2 = st.columns([2, 1])
with c2, st.form("new_case", clear_on_submit=True):
    st.markdown("**Open a new case**")
    title = st.text_input("Title", max_chars=200)
    desc = st.text_area("Description", height=80)
    if st.form_submit_button("Create case"):
        try:
            st.session_state["case_id"] = cases.create_case(title, desc)
            st.rerun()
        except cases.CaseError as exc:
            st.error(str(exc))
with c1:
    if cl.empty:
        st.info("No cases yet. Create one, or add a detection from the Anomaly Detection page.")
        st.stop()
    st.dataframe(cl, hide_index=True, use_container_width=True, height=180)
    ids = cl.case_id.tolist()
    cur = st.session_state.get("case_id", ids[0])
    case_id = st.selectbox(
        "Open case",
        ids,
        index=ids.index(cur) if cur in ids else 0,
        format_func=lambda i: f"{i}: {cl.set_index('case_id').title[i]}",
    )
    st.session_state["case_id"] = case_id

case = cases.get_case(case_id)
st.divider()
st.subheader(f"Case {case_id}: {case['title']}")
st.write(case.get("description") or "")
s1, s2 = st.columns([1, 2])
with s1:
    new_status = st.selectbox(
        "Status", cases.CASE_STATUSES, index=cases.CASE_STATUSES.index(case["status"])
    )
    reason = st.text_input("Reason (required to resolve or close)")
    if st.button("Update status") and new_status != case["status"]:
        try:
            cases.set_status(case_id, new_status, reason)
            st.rerun()
        except cases.CaseError as exc:
            st.error(str(exc))
with s2:
    st.markdown("**Status history**")
    st.dataframe(
        cases.status_history(case_id), hide_index=True, use_container_width=True, height=150
    )

ev = evidence.list_evidence(case_id)
st.subheader(f"Evidence ({len(ev)} items)")
t1, t2, t3, t4 = st.tabs(["Detections", "Observations", "Notes", "Provenance"])
with t1:
    a = ev[ev.evidence_type == "anomaly"]
    st.dataframe(
        a[
            [
                "anomaly_event_id",
                "anomaly_type",
                "detector",
                "anomaly_mmsi",
                "event_time",
                "score",
                "explanation",
            ]
        ],
        hide_index=True,
        use_container_width=True,
    )
    with st.form("add_anomaly"):
        eid = st.number_input("Add detection by event_id", min_value=1, step=1)
        inc = st.checkbox("Include supporting fixes", value=True)
        if st.form_submit_button("Attach detection"):
            try:
                evidence.add_anomaly(case_id, int(eid), include_positions=inc)
                st.rerun()
            except cases.CaseError as exc:
                st.error(str(exc))
with t2:
    p = ev[ev.evidence_type == "position"]
    st.dataframe(
        p[["position_id", "position_mmsi", "base_datetime", "lat", "lon", "sog", "added_at"]],
        hide_index=True,
        use_container_width=True,
        height=240,
    )
    with st.form("add_pos"):
        raw = st.text_input("Add observations by position_id (comma separated)")
        if st.form_submit_button("Attach observations"):
            try:
                pids = [int(x) for x in raw.replace(" ", "").split(",") if x]
                n = evidence.add_positions(case_id, pids)
                st.success(f"Attached {n} new observation(s).")
                st.rerun()
            except ValueError:
                st.error("Use numeric position ids, e.g. 1039046, 1039877")
with t3:
    n = ev[ev.evidence_type == "note"]
    for r in n.itertuples():
        st.markdown(f"*{r.added_at:%Y-%m-%d %H:%M} UTC, {r.added_by}:* {r.note}")
    with st.form("note", clear_on_submit=True):
        note = st.text_area("Add a note", height=90)
        if st.form_submit_button("Save note"):
            try:
                evidence.add_note(case_id, note)
                st.rerun()
            except cases.CaseError as exc:
                st.error(str(exc))
with t4:
    for r in ev[ev.provenance.notna() & (ev.evidence_type != "note")].head(30).itertuples():
        prov = r.provenance if isinstance(r.provenance, dict) else json.loads(r.provenance)
        with st.expander(f"{r.evidence_type} evidence {r.evidence_id}"):
            st.json(prov)

st.subheader("Export analytical summary")
rep = reporting.case_report(case_id)
e1, e2, e3, e4 = st.columns(4)
e1.download_button("HTML", rep.to_html().encode(), f"case_{case_id}.html", "text/html")
e2.download_button("Markdown", rep.to_markdown().encode(), f"case_{case_id}.md", "text/markdown")
e3.download_button("CSV", rep.to_csv(), f"case_{case_id}.csv", "text/csv")
e4.download_button("PDF", rep.to_pdf(), f"case_{case_id}.pdf", "application/pdf")
with st.expander("Preview"):
    st.markdown(rep.to_markdown())
