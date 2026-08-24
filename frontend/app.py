"""Streamlit UI for the IEEE SPS Committee Copilot."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# Import the backend package when running from the repo root (Streamlit Cloud).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Load .env here as well as in backend.config: the login gate reads the password
# before anything imports the backend, so without this a local run with a
# password set in .env would sit wide open while appearing to be protected.
load_dotenv(_ROOT / ".env")

# Two ways to reach the backend:
#   API_BASE set   -> talk to a running FastAPI server over HTTP (local dev)
#   API_BASE unset -> mount the same FastAPI app in this process (hosted)
# Hosted free tiers only run one process, and re-mounting the app reuses every
# endpoint unchanged rather than duplicating the routing here.
API = os.getenv("API_BASE", "").strip()
EMBEDDED = not API
TIMEOUT = 180

# Single accent hue used for every chart. One series per chart, so colour never
# has to encode identity and no categorical palette is needed.
ACCENT = "#00629B"  # IEEE blue

BAND_LABEL = {
    "priority": "PRIORITY",
    "promising": "Promising",
    "watch": "Watch",
    "low": "Low",
}
URGENCY_LABEL = {
    "critical": "CRITICAL",
    "urgent": "Urgent",
    "soon": "Soon",
    "later": "Later",
    "passed": "Passed",
}

st.set_page_config(page_title="IEEE SPS Committee Copilot", page_icon="::", layout="wide")


# ---------------------------------------------------------------------------
# access gate
# ---------------------------------------------------------------------------
def _expected_password() -> str:
    """Shared committee password, from Streamlit secrets or the environment."""
    try:
        if "APP_PASSWORD" in st.secrets:
            return str(st.secrets["APP_PASSWORD"]).strip()
    except Exception:  # noqa: BLE001 - no secrets.toml locally is normal
        pass
    return (os.getenv("APP_PASSWORD") or "").strip()


def require_login() -> None:
    """Gate the app behind one shared password.

    Outreach notes, organiser contacts and draft letters live in here, so a
    public URL should not be readable by whoever finds it. With no password
    configured the app stays open - that keeps local use frictionless.
    """
    expected = _expected_password()
    if not expected or st.session_state.get("authed"):
        return

    st.title("IEEE SPS Committee Copilot")
    st.caption("Enter the committee password to continue.")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Open", type="primary"):
            if entered == expected:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("That password is not right. Ask the chapter chair.")
    st.stop()


require_login()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _embedded_client():
    """Mount the FastAPI app in-process and run its startup once."""
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    client.__enter__()  # runs lifespan: database init + knowledge bootstrap
    return client


def _request(method: str, path: str, payload: Any = None, params: Any = None):
    if EMBEDDED:
        client = _embedded_client()
        if method == "GET":
            return client.get(path, params=params)
        if method == "DELETE":
            return client.delete(path, params=params)
        return client.post(path, json=payload, params=params)

    url = f"{API}{path}"
    if method == "GET":
        return requests.get(url, params=params, timeout=TIMEOUT)
    if method == "DELETE":
        return requests.delete(url, params=params, timeout=TIMEOUT)
    return requests.post(url, json=payload, params=params, timeout=TIMEOUT)


def api_get(path: str, **params: Any) -> dict[str, Any] | None:
    try:
        r = _request("GET", path, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 - surface any transport failure in the UI
        st.error(f"Backend request failed ({path}): {exc}")
        return None


def api_post(path: str, payload: Any = None, **params: Any) -> dict[str, Any] | None:
    try:
        r = _request("POST", path, payload=payload, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Backend request failed ({path}): {exc}")
        return None


def api_delete(path: str) -> None:
    try:
        _request("DELETE", path)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Delete failed: {exc}")


@st.cache_data(ttl=60)
def cached_health() -> dict[str, Any] | None:
    return api_get("/health")


def fmt_money(value: float) -> str:
    return f"INR {value:,.0f}"


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------
health = cached_health()
if health is None:
    st.title("IEEE SPS Committee Copilot")
    st.error(
        "The backend failed to start in this process."
        if EMBEDDED
        else f"Cannot reach the backend at {API}.\n\n"
        "Start it with `./run.ps1`, or `python -m uvicorn backend.main:app --port 8000`."
    )
    st.stop()

st.sidebar.title("SPS Copilot")
st.sidebar.caption(f"{health['chapter']}\n\n{health['institution']}")

PAGES = [
    "Dashboard",
    "Conference tracker",
    "Event advisor",
    "Outreach",
    "Playbook",
    "Activity report",
    "Settings",
]
page = st.sidebar.radio("Go to", PAGES, label_visibility="collapsed")

stats = health["stats"]
st.sidebar.divider()
st.sidebar.metric("Events tracked", stats["conferences"])
st.sidebar.metric("In outreach", stats["active_outreach"])
llm_state = health["llm"]
st.sidebar.caption(
    f"LLM: {llm_state['provider']} "
    + ("(active)" if llm_state["configured"] else "(not configured - rules only)")
)
st.sidebar.caption(f"Last crawl: {stats['last_crawl'][:16].replace('T', ' ')}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
if page == "Dashboard":
    st.title("Dashboard")

    confs = api_get("/conferences", limit=200) or {"conferences": []}
    rows = confs["conferences"]
    radar_data = api_get("/radar", critical_only=True) or {"items": [], "summary": {}}

    priority = [c for c in rows if c.get("band") == "priority"]
    promising = [c for c in rows if c.get("band") == "promising"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Priority opportunities", len(priority))
    c2.metric("Promising", len(promising))
    c3.metric("Deadlines needing action", len(radar_data["items"]))
    c4.metric("Events logged", stats["events_run"])

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Worth approaching now")
        shortlist = (priority + promising)[:8]
        if not shortlist:
            st.info("Nothing above the promising threshold yet. Run a crawl from Settings.")
        for c in shortlist:
            with st.container(border=True):
                head, action = st.columns([5, 1])
                with head:
                    st.markdown(f"**{c['title']}**")
                    meta = [
                        BAND_LABEL.get(c.get("band", ""), ""),
                        f"fit {c['fit_score']}",
                        c.get("start_date") or "dates TBC",
                        c.get("location") or "venue TBC",
                    ]
                    st.caption(" &middot; ".join(m for m in meta if m))
                    if c.get("proposal_deadline"):
                        st.markdown(f":red[**Proposal due {c['proposal_deadline']}**]")
                with action:
                    if st.button("Open", key=f"dash_open_{c['id']}"):
                        st.session_state["focus_conference"] = c["id"]
                        st.session_state["goto"] = "Conference tracker"
                        st.rerun()
                if c.get("fit_reasons"):
                    with st.expander("Why this scored where it did"):
                        for reason in c["fit_reasons"]:
                            st.write(f"- {reason}")

    with right:
        st.subheader("Deadline radar")
        if not radar_data["items"]:
            st.success("No binding deadlines in the next 30 days.")
        for item in radar_data["items"][:10]:
            label = URGENCY_LABEL.get(item["urgency"], item["urgency"])
            with st.container(border=True):
                st.markdown(f"**{label}** &middot; {item['date']} ({item['days']}d)")
                st.caption(f"{item['label']}: {item['title'][:70]}")

        summary = radar_data.get("summary") or {}
        if summary:
            st.caption(
                " &middot; ".join(f"{URGENCY_LABEL.get(k, k)}: {v}" for k, v in summary.items())
            )


# ---------------------------------------------------------------------------
# Conference tracker
# ---------------------------------------------------------------------------
elif page == "Conference tracker":
    st.title("Conference tracker")
    st.caption(
        "Every IEEE and signal-processing event we have found, scored for how worth "
        "approaching it is. Score explains itself - open a row to see why."
    )

    with st.expander(
        "Add a conference manually - for leads from your Section, faculty or a mailing list"
    ):
        st.caption(
            "IEEE's own conference search blocks automated access, so India-based "
            "conferences often reach you by word of mouth first. Anything added here is "
            "scored, tracked and worked exactly like a crawled one."
        )
        with st.form("manual_conf"):
            m1, m2 = st.columns([3, 1])
            m_title = m1.text_input("Conference title *", placeholder="2027 IEEE INDICON")
            m_acronym = m2.text_input("Acronym", placeholder="INDICON")
            m3, m4, m5 = st.columns(3)
            m_location = m3.text_input("Location", placeholder="Bengaluru, India")
            m_start = m4.text_input("Start date", placeholder="2027-12-15")
            m_end = m5.text_input("End date", placeholder="2027-12-17")
            m6, m7 = st.columns(2)
            m_cfp = m6.text_input("Paper deadline", placeholder="2027-07-01")
            m_prop = m7.text_input("Hosting/proposal deadline", placeholder="2027-02-28")
            m8, m9 = st.columns(2)
            m_url = m8.text_input("Website")
            m_kind = m9.selectbox(
                "Type",
                ["conference", "call_for_organizers", "cfp"],
                format_func=lambda k: k.replace("_", " "),
            )
            m_summary = st.text_area("Notes / summary", height=70)
            if st.form_submit_button("Add and score", type="primary"):
                if not m_title.strip():
                    st.warning("A title is required.")
                else:
                    created = api_post(
                        "/conferences",
                        {
                            "title": m_title,
                            "acronym": m_acronym,
                            "location": m_location,
                            "start_date": m_start,
                            "end_date": m_end,
                            "cfp_deadline": m_cfp,
                            "proposal_deadline": m_prop,
                            "homepage": m_url,
                            "kind": m_kind,
                            "summary": m_summary,
                        },
                    )
                    if created:
                        st.success(
                            f"Added **{created['title']}** - fit score "
                            f"{created['fit_score']} ({created['band']})"
                        )
                        for reason in created.get("fit_reasons", []):
                            st.caption(f"- {reason}")

    f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
    min_score = f1.slider("Minimum fit", 0, 100, 40, step=5)
    kind = f2.selectbox(
        "Type", ["any", "call_for_organizers", "conference", "cfp"], format_func=lambda k: k.replace("_", " ")
    )
    status = f3.selectbox("Status", ["any", "new", "watching", "dismissed"])
    search = f4.text_input("Search title")

    params: dict[str, Any] = {"min_score": min_score, "limit": 300}
    if kind != "any":
        params["kind"] = kind
    if status != "any":
        params["status"] = status

    data = api_get("/conferences", **params) or {"conferences": []}
    rows = data["conferences"]
    if search:
        rows = [r for r in rows if search.lower() in r["title"].lower()]

    st.write(f"**{len(rows)}** matching events")

    focus = st.session_state.pop("focus_conference", None)
    for c in rows[:60]:
        expanded = focus == c["id"]
        header = f"[{c['fit_score']}] {c['title'][:88]}"
        with st.expander(header, expanded=expanded):
            meta = st.columns(4)
            meta[0].markdown(f"**Fit**  \n{c['fit_score']} ({BAND_LABEL.get(c.get('band',''),'')})")
            meta[1].markdown(f"**Dates**  \n{c.get('start_date') or 'TBC'}")
            meta[2].markdown(f"**Location**  \n{c.get('location') or 'TBC'}")
            meta[3].markdown(f"**Type**  \n{(c.get('kind') or '').replace('_',' ')}")

            if c.get("proposal_deadline"):
                st.warning(f"Hosting proposal deadline: **{c['proposal_deadline']}**")
            if c.get("cfp_deadline"):
                st.caption(f"Paper deadline: {c['cfp_deadline']}")
            if c.get("summary"):
                st.caption(c["summary"][:400])

            links = []
            if c.get("url"):
                links.append(f"[Listing]({c['url']})")
            if c.get("homepage"):
                links.append(f"[Conference site]({c['homepage']})")
            if links:
                st.markdown(" &middot; ".join(links))

            if c.get("fit_reasons"):
                st.markdown("**Why this score**")
                for reason in c["fit_reasons"]:
                    st.write(f"- {reason}")

            b1, b2, b3 = st.columns(3)
            if b1.button("Watch", key=f"watch_{c['id']}"):
                api_post(f"/conferences/{c['id']}/status", {"status": "watching"})
                st.rerun()
            if b2.button("Dismiss", key=f"dismiss_{c['id']}"):
                api_post(f"/conferences/{c['id']}/status", {"status": "dismissed"})
                st.rerun()
            if b3.button("Draft outreach email", key=f"draft_{c['id']}"):
                draft = api_post(f"/outreach/{c['id']}/draft", {})
                if draft:
                    st.session_state[f"draft_{c['id']}"] = draft

            saved = st.session_state.get(f"draft_{c['id']}")
            if saved:
                st.text_area(
                    f"Draft ({saved['source']})",
                    saved["draft"],
                    height=320,
                    key=f"draft_box_{c['id']}",
                )
                st.caption("Copy this into your mail client, then set the stage on the Outreach page.")


# ---------------------------------------------------------------------------
# Event advisor
# ---------------------------------------------------------------------------
elif page == "Event advisor":
    st.title("Event advisor")
    st.caption(
        "Pitch an event or a collaboration. Feedback is grounded in past IEEE SPS "
        "events and real programme budgets, not guesswork."
    )

    tab_pitch, tab_chat, tab_history = st.tabs(["Evaluate a pitch", "Ask a question", "Past pitches"])

    with tab_pitch:
        with st.form("pitch_form"):
            title = st.text_input("Working title", placeholder="SP for Healthcare: EEG Datathon")
            idea = st.text_area(
                "What is the idea?",
                height=140,
                placeholder="Describe what attendees will actually do, who it is for, and why now.",
            )
            col1, col2, col3 = st.columns(3)
            fmt = col1.selectbox(
                "Format",
                ["", "webinar", "workshop", "hackathon", "symposium", "seasonal_school",
                 "distinguished_lecture", "sp_cup", "paper_bootcamp", "industry_visit",
                 "chapter_initiative", "conference_host"],
                format_func=lambda s: s.replace("_", " ").title() if s else "Let the advisor decide",
            )
            audience = col2.text_input("Expected audience size", placeholder="80")
            co_society = col3.text_input("Co-hosting society", placeholder="RAS, EMBS, WIE...")
            submitted = st.form_submit_button("Get feedback", type="primary")

        if submitted:
            if not idea.strip():
                st.warning("Describe the idea first.")
            else:
                with st.spinner("Checking against past SPS events..."):
                    result = api_post(
                        "/advisor/evaluate",
                        {
                            "title": title,
                            "idea": idea,
                            "format": fmt,
                            "audience": audience,
                            "co_society": co_society,
                        },
                    )
                if result:
                    st.session_state["last_eval"] = result

        result = st.session_state.get("last_eval")
        if result:
            st.divider()
            v1, v2 = st.columns([1, 3])
            v1.metric("Verdict", result["verdict"].title(), f"{result['score']}/100")
            v2.info(result["summary"])
            if result.get("differentiator"):
                v2.caption(f"**What would make it distinctive:** {result['differentiator']}")

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Strengths")
                for s in result["strengths"]:
                    st.success(s, icon=None)
            with c2:
                st.subheader("Risks")
                for r in result["risks"]:
                    st.warning(r, icon=None)

            fmt_info = result.get("suggested_format")
            if fmt_info:
                st.subheader("Format economics")
                m = st.columns(4)
                m[0].metric("Format", fmt_info["name"])
                m[1].metric("Lead time", f"{fmt_info['lead_weeks']} wks")
                m[2].metric(
                    "Budget range",
                    f"{fmt_info['budget_min']//1000}k-{fmt_info['budget_max']//1000}k",
                )
                m[3].metric("Volunteers", fmt_info["volunteers"])
                if fmt_info.get("sps_program"):
                    st.success(f"Supporting IEEE programme: **{fmt_info['sps_program']}**")
                if fmt_info.get("notes"):
                    st.caption(fmt_info["notes"])

            if result.get("co_societies"):
                st.subheader("Who to co-host with")
                for s in result["co_societies"]:
                    with st.container(border=True):
                        st.markdown(f"**{s['abbr']}** - {s['name']}")
                        st.caption(s["pitch_angle"])

            if result.get("precedent"):
                st.subheader("Past IEEE SPS events like this")
                for p in result["precedent"]:
                    line = f"- {p['title']}"
                    if p.get("date"):
                        line += f" ({p['date']})"
                    if p.get("url"):
                        line = f"- [{p['title']}]({p['url']})" + (f" ({p['date']})" if p.get("date") else "")
                    st.markdown(line)

            st.subheader("Next steps")
            for i, step in enumerate(result["next_steps"], 1):
                st.write(f"{i}. {step}")

            if result.get("timeline"):
                st.subheader("Indicative timeline")
                st.dataframe(
                    pd.DataFrame(result["timeline"])[["date", "task"]],
                    hide_index=True,
                    width="stretch",
                )
            st.caption(f"Assessment source: {result.get('source')}")

    with tab_chat:
        if "chat" not in st.session_state:
            st.session_state["chat"] = []
        for msg in st.session_state["chat"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        prompt = st.chat_input("e.g. Which society should we partner with for an audio ML event?")
        if prompt:
            st.session_state["chat"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    resp = api_post(
                        "/advisor/chat",
                        {"message": prompt, "history": st.session_state["chat"][:-1]},
                    )
                reply = resp["reply"] if resp else "The backend did not respond."
                st.markdown(reply)
            st.session_state["chat"].append({"role": "assistant", "content": reply})

        if st.session_state["chat"] and st.button("Clear conversation"):
            st.session_state["chat"] = []
            st.rerun()

    with tab_history:
        pitches = api_get("/pitches") or {"pitches": []}
        if not pitches["pitches"]:
            st.info("No pitches evaluated yet.")
        for p in pitches["pitches"]:
            with st.expander(f"[{p.get('score')}] {p['title'] or p['idea'][:60]} - {p.get('status')}"):
                st.write(p["idea"])
                st.caption(f"Verdict: {p.get('verdict')} | Created {p['created_at'][:10]}")
                cols = st.columns(4)
                for i, new_status in enumerate(["approved", "scheduled", "done", "shelved"]):
                    if cols[i].button(new_status.title(), key=f"pstatus_{p['id']}_{new_status}"):
                        api_post(f"/pitches/{p['id']}/status", {"status": new_status})
                        st.rerun()


# ---------------------------------------------------------------------------
# Outreach
# ---------------------------------------------------------------------------
elif page == "Outreach":
    st.title("Outreach pipeline")
    st.caption("Every conference we are actively pursuing, and what has to happen next.")

    data = api_get("/outreach") or {"pipeline": {}, "stages": [], "funnel": {}}
    funnel = data["funnel"]
    stage_labels = {s["id"]: s["label"] for s in data["stages"]}

    cols = st.columns(len(funnel) or 1)
    for col, (stage, count) in zip(cols, funnel.items()):
        col.metric(stage.replace("_", " ").title(), count)

    st.divider()

    any_rows = False
    for stage in [s["id"] for s in data["stages"]]:
        rows = data["pipeline"].get(stage, [])
        if not rows:
            continue
        any_rows = True
        st.subheader(f"{stage.replace('_',' ').title()} ({len(rows)})")
        st.caption(stage_labels.get(stage, ""))
        for row in rows:
            with st.container(border=True):
                top, act = st.columns([4, 1])
                with top:
                    st.markdown(f"**{row.get('title', 'Conference')}**")
                    bits = [
                        f"fit {row.get('fit_score', 0)}",
                        row.get("start_date") or "dates TBC",
                        row.get("location") or "",
                    ]
                    st.caption(" &middot; ".join(b for b in bits if b))
                    if row.get("next_action"):
                        st.info(
                            f"Next: {row['next_action']}"
                            + (f" (by {row['next_action_date']})" if row.get("next_action_date") else "")
                        )
                    if row.get("contact_name") or row.get("contact_email"):
                        st.caption(
                            f"Contact: {row.get('contact_name') or '?'} "
                            f"{row.get('contact_email') or ''} {row.get('contact_role') or ''}"
                        )
                    if row.get("notes"):
                        with st.expander("Notes"):
                            st.text(row["notes"])
                with act:
                    new_stage = st.selectbox(
                        "Move to",
                        [s["id"] for s in data["stages"]],
                        index=[s["id"] for s in data["stages"]].index(stage),
                        key=f"stage_{row['conference_id']}",
                        label_visibility="collapsed",
                    )
                    if new_stage != stage and st.button("Update", key=f"upd_{row['conference_id']}"):
                        api_post(
                            f"/outreach/{row['conference_id']}/stage",
                            {"stage": new_stage, "note": f"Moved to {new_stage}"},
                        )
                        st.rerun()

                with st.expander("Record contact details"):
                    with st.form(f"contact_{row['conference_id']}"):
                        cn = st.text_input("Contact name", row.get("contact_name") or "")
                        ce = st.text_input("Contact email", row.get("contact_email") or "")
                        cr = st.text_input("Role", row.get("contact_role") or "", placeholder="General Chair")
                        na = st.text_input("Next action", row.get("next_action") or "")
                        nd = st.text_input("By date (YYYY-MM-DD)", row.get("next_action_date") or "")
                        if st.form_submit_button("Save"):
                            api_post(
                                f"/outreach/{row['conference_id']}/contact",
                                {
                                    "contact_name": cn,
                                    "contact_email": ce,
                                    "contact_role": cr,
                                    "next_action": na,
                                    "next_action_date": nd,
                                },
                            )
                            st.rerun()

                if row.get("draft"):
                    with st.expander("Email draft"):
                        st.text_area(
                            "Draft", row["draft"], height=300, key=f"od_{row['conference_id']}"
                        )

    if not any_rows:
        st.info(
            "Nothing in the pipeline yet. Open the Conference tracker, pick a target and "
            "use **Draft outreach email** to start one."
        )


# ---------------------------------------------------------------------------
# Playbook
# ---------------------------------------------------------------------------
elif page == "Playbook":
    st.title("Event playbook")
    st.caption("Turn an approved idea into a budget, a timeline and a role split.")

    pitches = api_get("/pitches") or {"pitches": []}
    options = {0: "-- start from scratch --"}
    options.update({p["id"]: f"[{p.get('score')}] {p['title'] or p['idea'][:50]}" for p in pitches["pitches"]})

    with st.form("playbook_form"):
        chosen = st.selectbox("Base it on a pitch", list(options), format_func=lambda k: options[k])
        c1, c2 = st.columns(2)
        title = c1.text_input("Event title", placeholder="Leave blank to use the pitch title")
        fmt = c2.selectbox(
            "Format",
            ["", "webinar", "workshop", "hackathon", "symposium", "seasonal_school",
             "distinguished_lecture", "sp_cup", "paper_bootcamp", "industry_visit",
             "chapter_initiative", "conference_host"],
            format_func=lambda s: s.replace("_", " ").title() if s else "Infer from the idea",
        )
        idea = st.text_area("Idea (if starting from scratch)", height=100)
        c3, c4 = st.columns(2)
        event_date = c3.date_input("Target date", value=None)
        budget = c4.number_input("Budget override (INR, 0 = use the typical figure)", 0, step=5000)
        go = st.form_submit_button("Generate playbook", type="primary")

    if go:
        payload: dict[str, Any] = {
            "title": title,
            "idea": idea,
            "format": fmt,
            "event_date": event_date.isoformat() if event_date else None,
            "budget_total": int(budget) or None,
        }
        if chosen:
            payload["pitch_id"] = chosen
        with st.spinner("Building the plan..."):
            plan = api_post("/playbook", payload)
        if plan:
            st.session_state["last_plan"] = plan

    plan = st.session_state.get("last_plan")
    if plan:
        st.divider()
        st.subheader(plan["title"])
        m = st.columns(4)
        m[0].metric("Format", plan["format"]["name"])
        m[1].metric("Target date", plan["event_date"])
        m[2].metric("Budget", fmt_money(plan["budget"]["total"]))
        m[3].metric("Volunteers", plan["format"].get("volunteers", "-"))
        if plan.get("funding_programme"):
            st.success(f"Funding route: **{plan['funding_programme']}**")

        tabs = st.tabs(["Budget", "Timeline", "Roles", "Approvals", "Promotion", "Full document"])

        with tabs[0]:
            budget_df = pd.DataFrame(plan["budget"]["lines"])
            st.dataframe(
                budget_df.rename(columns={"item": "Line item", "share": "Share %", "amount": "INR"}),
                hide_index=True,
                width="stretch",
            )
            st.bar_chart(budget_df, x="item", y="amount", color=ACCENT, horizontal=True)

        with tabs[1]:
            st.dataframe(
                pd.DataFrame(plan["milestones"])[["date", "task"]],
                hide_index=True,
                width="stretch",
            )

        with tabs[2]:
            for r in plan["roles"]:
                st.markdown(f"**{r['role']}** - {r['responsibility']}")
            if plan.get("speaker_profiles"):
                st.subheader("Speakers to target")
                for s in plan["speaker_profiles"]:
                    st.write(f"- {s}")

        with tabs[3]:
            for a in plan["approvals"]:
                st.write(f"- {a}")
            risks = list(plan.get("watch_outs") or []) + list(plan.get("risks") or [])
            if risks:
                st.subheader("Risks")
                for r in dict.fromkeys(risks):
                    st.warning(r, icon=None)

        with tabs[4]:
            st.dataframe(
                pd.DataFrame(plan["promotion"]).rename(columns={"date": "Date", "action": "Action"}),
                hide_index=True,
                width="stretch",
            )
            if plan.get("sponsor_targets"):
                st.subheader("Sponsor targets")
                for s in plan["sponsor_targets"]:
                    st.write(f"- {s}")

        with tabs[5]:
            st.markdown(plan["markdown"])
            st.download_button(
                "Download as Markdown",
                plan["markdown"],
                file_name=f"playbook-{plan['title'][:40].replace(' ', '-').lower()}.md",
                mime="text/markdown",
            )


# ---------------------------------------------------------------------------
# Activity report
# ---------------------------------------------------------------------------
elif page == "Activity report":
    st.title("Chapter activity report")
    st.caption("Log events as you run them; the annual report then writes itself.")

    tab_log, tab_report, tab_trend = st.tabs(["Log an event", "Annual report", "Trend"])

    with tab_log:
        with st.form("event_form"):
            c1, c2 = st.columns(2)
            title = c1.text_input("Event title")
            event_date = c2.date_input("Date held", value=date.today())
            c3, c4, c5 = st.columns(3)
            fmt = c3.text_input("Format", placeholder="workshop")
            co_society = c4.text_input("Co-hosting society", placeholder="RAS")
            speakers = c5.text_input("Speakers")
            c6, c7, c8 = st.columns(3)
            attendance = c6.number_input("Attendance", 0, step=5)
            volunteers = c7.number_input("Volunteers", 0, step=1)
            spend = c8.number_input("Spend (INR)", 0.0, step=1000.0)
            outcomes = st.text_area("Outcomes / notes", height=90)
            if st.form_submit_button("Log event", type="primary"):
                if not title.strip():
                    st.warning("The event needs a title.")
                else:
                    api_post(
                        "/events",
                        {
                            "title": title,
                            "event_date": event_date.isoformat(),
                            "format": fmt,
                            "co_society": co_society,
                            "speakers": speakers,
                            "attendance": int(attendance),
                            "volunteers": int(volunteers),
                            "budget_spent": float(spend),
                            "outcomes": outcomes,
                        },
                    )
                    st.success(f"Logged: {title}")
                    st.rerun()

        logged = api_get("/events") or {"events": []}
        if logged["events"]:
            st.subheader("Logged events")
            df = pd.DataFrame(logged["events"])
            st.dataframe(
                df[["event_date", "title", "format", "co_society", "attendance", "volunteers"]],
                hide_index=True,
                width="stretch",
            )
            to_delete = st.selectbox(
                "Remove an entry",
                [0] + [e["id"] for e in logged["events"]],
                format_func=lambda i: "-" if not i else next(
                    e["title"] for e in logged["events"] if e["id"] == i
                ),
            )
            if to_delete and st.button("Delete", type="secondary"):
                api_delete(f"/events/{to_delete}")
                st.rerun()

    with tab_report:
        years = (api_get("/events") or {}).get("years") or [date.today().year]
        year = st.selectbox("Reporting year", years)
        want_narrative = st.checkbox(
            "Include an AI-written year-in-review",
            value=False,
            help="Requires an LLM key. Figures always come from your logged events.",
        )
        if st.button("Generate report", type="primary"):
            with st.spinner("Compiling..."):
                rep = api_get(f"/report/{year}", with_narrative=want_narrative)
            if rep:
                st.session_state["last_report"] = rep

        rep = st.session_state.get("last_report")
        if rep:
            m = st.columns(5)
            m[0].metric("Events", rep["event_count"])
            m[1].metric("Attendance", rep["total_attendance"])
            m[2].metric("Avg / event", rep["avg_attendance"])
            m[3].metric("Volunteers", rep["total_volunteers"])
            m[4].metric("Spend", fmt_money(rep["total_spend"]))

            if rep["by_format"]:
                st.subheader("Activity mix")
                mix = pd.DataFrame(
                    sorted(rep["by_format"].items(), key=lambda kv: -kv[1]),
                    columns=["Format", "Events"],
                )
                st.bar_chart(mix, x="Format", y="Events", color=ACCENT, horizontal=True)
                st.dataframe(mix, hide_index=True, width="stretch")

            st.subheader("Report document")
            st.markdown(rep["markdown"])
            d1, d2 = st.columns(2)
            d1.download_button(
                "Download Markdown",
                rep["markdown"],
                file_name=f"sps-chapter-report-{rep['year']}.md",
                mime="text/markdown",
            )
            d2.download_button(
                "Download CSV",
                rep["csv"],
                file_name=f"sps-chapter-events-{rep['year']}.csv",
                mime="text/csv",
            )

    with tab_trend:
        trend = api_get("/report/trend/all") or {"trend": []}
        if len(trend["trend"]) < 1:
            st.info("Log events across more than one year to see a trend.")
        else:
            tdf = pd.DataFrame(trend["trend"])
            st.subheader("Events per year")
            st.bar_chart(tdf, x="year", y="event_count", color=ACCENT)
            st.subheader("Attendance per year")
            st.bar_chart(tdf, x="year", y="total_attendance", color=ACCENT)
            st.dataframe(tdf, hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
elif page == "Settings":
    st.title("Settings and operations")

    db = health.get("database") or {}
    st.subheader("Where the data is stored")
    if db.get("persistent"):
        st.success(
            f"**Postgres** at `{db.get('location')}` - data is permanent and shared "
            "across everyone using this link."
        )
    else:
        st.error(
            f"**SQLite file** (`{db.get('location')}`) - data is NOT permanent on a hosted "
            "deployment.\n\n"
            "That is correct when running on your own laptop. If you are seeing this on the "
            "shared link, `DATABASE_URL` is missing or misspelled in the Streamlit secrets, "
            "and everything logged here will be wiped the next time the app restarts. "
            "Fix that before the committee starts using it."
        )

    cfg = api_get("/config") or {"chapter": {}}
    st.subheader("Chapter profile")
    st.caption("Edit these in the `.env` file, then restart the backend.")
    st.json(cfg["chapter"])

    st.divider()
    st.subheader("Data collection")
    c1, c2, c3 = st.columns(3)
    if c1.button("Run a crawl now", type="primary"):
        with st.spinner("Crawling IEEE SPS and WikiCFP..."):
            res = api_post("/crawl", None, use_cache=False)
        if res:
            st.success(f"{res['new']} new, {res['updated']} updated in {res['seconds']}s")
            st.json(res["sources"])
            if res["errors"]:
                st.warning(res["errors"])
        cached_health.clear()
    if c2.button("Rescore everything"):
        res = api_post("/rescore")
        if res:
            st.success(f"Rescored {res['rescored']} events")
    if c3.button("Refresh past-event knowledge"):
        with st.spinner("Harvesting the SPS archive..."):
            res = api_post("/knowledge/refresh", None, pages=12)
        if res:
            st.success(f"Stored {res['past_events']} past events")

    st.caption(f"Knowledge base: {health['knowledge']}")

    st.divider()
    st.subheader("Notifications")
    ch = health["notify"]
    n1, n2 = st.columns(2)
    n1.metric("Telegram", "Configured" if ch["telegram"]["configured"] else "Not set up")
    n2.metric(
        "Email",
        "Configured" if ch["email"]["configured"] else "Not set up",
        f"{ch['email']['recipients']} recipients",
    )

    if not ch["telegram"]["configured"]:
        st.info(
            "**Telegram setup:** create a bot with @BotFather, put the token in `.env` as "
            "`TELEGRAM_BOT_TOKEN`, add the bot to your committee group, send any message "
            "there, then press *Find chat id* below and copy the id into `TELEGRAM_CHAT_ID`."
        )
    if st.button("Find chat id"):
        res = api_get("/notify/telegram/chats")
        st.json(res or {})

    p1, p2 = st.columns(2)
    if p1.button("Preview digest"):
        res = api_post("/notify/digest", None, dry_run=True)
        if res:
            st.code(res["preview"], language="markdown")
    if p2.button("Send digest now"):
        res = api_post("/notify/digest", None, dry_run=False)
        st.json(res or {})

    a1, a2 = st.columns(2)
    if a1.button("Preview pending alerts"):
        res = api_post("/notify/alerts", None, dry_run=True)
        if res:
            st.code(res.get("preview", res.get("reason", "nothing pending")), language="markdown")
    if a2.button("Send alerts now"):
        res = api_post("/notify/alerts", None, dry_run=False)
        st.json(res or {})

    st.divider()
    st.subheader("Scheduler")
    sched = api_get("/scheduler") or {}
    st.write("Running" if sched.get("running") else "Not running")
    for job in sched.get("jobs", []):
        nxt = job.get("next_run")
        st.caption(f"**{job['id']}** - next run {nxt[:16].replace('T',' ') if nxt else 'unscheduled'}")
    if sched.get("history"):
        with st.expander("Recent job runs"):
            for h in sched["history"]:
                st.caption(f"{h['at'][:16].replace('T',' ')} - {h['job']} - {h.get('error') or 'ok'}")

    st.divider()
    st.subheader("LLM")
    st.json(health["llm"])
    if not health["llm"]["configured"]:
        st.info(
            "Running in rules-only mode: scoring, tracking, budgets, timelines and reports "
            "all work. Add `GEMINI_API_KEY` to `.env` for written feedback and drafted emails."
        )
