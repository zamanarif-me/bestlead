"""Best Lead — Outscraper Lead Scraper (Digital Zeon).

Pipeline:  scrape -> enrich -> validate -> score -> icebreaker -> dedup
                  -> cross-session history check -> export

Run:  streamlit run app.py
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from modules import dedup, enrichment, exporter, history, icebreaker, scoring
from modules.scraper import OutscraperClient, ScrapeConfig
from modules.validator import EmailValidator

load_dotenv()  # must run before anything reads os.getenv

st.set_page_config(page_title="Best Lead", page_icon="🎯", layout="wide")
st.title("🎯 Best Lead")
st.caption("Google Maps → enriched, validated, scored home-service leads → Instantly / Call-list CSV")


# ─────────────────────────── Sidebar ───────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    api_key = st.text_input("Outscraper API key", value=os.getenv("OUTSCRAPER_API_KEY", ""),
                            type="password", help="Loaded from .env; override here if needed.")

    run_mode = st.radio("Run mode", ["Sync", "Async"], horizontal=True,
                        help="Async is recommended for 500+ leads.")
    limit = st.slider("Max results per location", 10, 500, 100, step=10)
    region = st.text_input("Region code", value=os.getenv("DEFAULT_REGION", "US"))
    language = st.text_input("Language", value=os.getenv("DEFAULT_LANGUAGE", "en"))

    st.divider()
    st.subheader("✉️ Email enrichment")
    enrich_mode = st.radio("Mode", ["Basic", "Full"], horizontal=True,
                           help="Basic = Maps emails only. Full = also crawl each domain (more emails + socials).")

    st.subheader("🛡️ Dead-email detection")
    st.checkbox("L1 · Syntax", value=True, disabled=True)
    lvl_mx = st.checkbox("L2 · MX (DNS)", value=True)
    lvl_smtp = st.checkbox("L3 · SMTP probe", value=False, help="Accurate but slow / sometimes blocked.")
    lvl_api = st.checkbox("L4 · Outscraper API", value=False, help="Paid, most reliable.")

    st.divider()
    drop_dupes_api = st.checkbox("Ask Outscraper to drop duplicates", value=True)


def selected_levels() -> tuple[str, ...]:
    levels = ["syntax"]
    if lvl_mx:
        levels.append("mx")
    if lvl_smtp:
        levels.append("smtp")
    if lvl_api:
        levels.append("api")
    return tuple(levels)


# ─────────────────────────── Pipeline ───────────────────────────
def run_pipeline(client: OutscraperClient, leads: list[dict], meta: dict) -> list[dict]:
    if not leads:
        return leads

    if enrich_mode == "Full":
        bar = st.progress(0.0, text="Enriching emails…")
        enrichment.enrich_leads(client, leads, mode="full",
                                progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Enriching {d}/{t} domains"))
        bar.empty()
    else:
        enrichment.enrich_leads(client, leads, mode="basic")

    validator = EmailValidator(client=client if lvl_api else None,
                               levels=selected_levels(), smtp_from=os.getenv("SMTP_FROM_EMAIL"))
    bar = st.progress(0.0, text="Validating emails…")
    validator.validate_leads(leads, progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Validating {d}/{t} emails"))
    bar.empty()

    scoring.score_leads(leads)
    icebreaker.add_icebreakers(leads)
    dedup.deduplicate(leads)
    history.flag_seen_before(leads)          # compare to PAST sessions
    session = history.save_session(leads, meta)   # persist + update index
    st.session_state["last_session"] = session
    return leads


# ─────────────────────────── Helpers ───────────────────────────
def collect_locations(uploaded, text: str) -> list[str]:
    locs: list[str] = []
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            col = "location" if "location" in df.columns else df.columns[0]
            locs += [str(v).strip() for v in df[col].dropna().tolist()]
        except Exception as e:
            st.warning(f"Couldn't read CSV: {e}")
    locs += [l.strip() for l in text.splitlines() if l.strip()]
    seen, out = set(), []
    for l in locs:
        if l.lower() not in seen:
            seen.add(l.lower())
            out.append(l)
    return out


def _email_icon(lead: dict) -> str:
    if not lead.get("email_best") or lead.get("email_status") == "Dead":
        return "🔴 Dead" if lead.get("emails") else ""
    return {"Direct": "🟢 Direct", "Generic": "🟡 Generic", "Personal": "🔵 Personal"} \
        .get(lead.get("email_best_type"), lead.get("email_best_type") or "")


def _label_icon(lead: dict) -> str:
    return {"Hot": "🔥 Hot", "Warm": "✅ Warm", "Cold": "💤 Cold"} \
        .get(lead.get("lead_label"), lead.get("lead_label") or "")


# ─────────────────────────── Tabs ───────────────────────────
tab_run, tab_results, tab_export = st.tabs(["🚀 Run Scraper", "📊 Results", "💾 Export"])

# ---------- Tab 1: Run ----------
with tab_run:
    c1, c2 = st.columns(2)
    with c1:
        niche = st.text_input("Service / niche keyword", placeholder="e.g. plumber, roofer, HVAC, electrician")
    with c2:
        uploaded = st.file_uploader("Bulk locations CSV (optional)", type=["csv"],
                                    help="Column named 'location', or the first column.")

    locations_text = st.text_area("Locations (one per line)",
                                  placeholder="New York, NY\n90210\nAustin, TX\nUnited Kingdom", height=120)

    go = st.button("🚀 Scrape leads", type="primary", use_container_width=True)

    if go:
        try:
            client = OutscraperClient(api_key=api_key)
            locations = collect_locations(uploaded, locations_text)
            cfg = ScrapeConfig(niche=niche, locations=locations, limit_per_query=limit,
                               language=language, region=region or None,
                               drop_duplicates=drop_dupes_api, extract_contacts=(enrich_mode == "Full"))
            meta = {"niche": niche, "locations": locations, "mode": enrich_mode, "run_mode": run_mode}

            if run_mode == "Sync":
                with st.spinner(f"Scraping {len(locations)} location(s)…"):
                    leads = client.search_sync(cfg)
                leads = run_pipeline(client, leads, meta)
                st.session_state["leads"] = leads
                st.success(f"Done — {len(leads)} leads scraped.")
            else:
                request_id = client.search_async(cfg)
                st.session_state["request_id"] = request_id
                st.session_state["pending_meta"] = meta
                st.session_state.pop("leads", None)
                st.info(f"Async job started. Job ID: `{request_id}`")
        except Exception as e:
            st.error(f"❌ {e}")

    # Async job tracker
    if st.session_state.get("request_id") and "leads" not in st.session_state:
        st.warning("⏳ Async job in progress.")
        st.code(st.session_state["request_id"], language=None)
        if st.button("🔄 Check job status"):
            try:
                client = OutscraperClient(api_key=api_key)
                res = client.poll_async(st.session_state["request_id"])
                if res["status"] == "Success":
                    leads = run_pipeline(client, res["leads"], st.session_state.get("pending_meta", {}))
                    st.session_state["leads"] = leads
                    st.session_state.pop("request_id", None)
                    st.success(f"Done — {len(leads)} leads.")
                elif res["status"] == "Error":
                    st.error("Outscraper reported an error for this job.")
                    st.session_state.pop("request_id", None)
                else:
                    st.info("Still pending… check again in a moment.")
            except Exception as e:
                st.error(f"❌ {e}")

    # Live stats
    if st.session_state.get("leads"):
        leads = st.session_state["leads"]
        dead = sum(1 for l in leads if l.get("email_status") == "Dead")
        emails = sum(1 for l in leads if l.get("email_best") and l.get("email_status") != "Dead")
        seen = sum(1 for l in leads if l.get("seen_before"))
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Leads found", len(leads))
        s2.metric("Emails found", emails)
        s3.metric("Dead emails", dead)
        s4.metric("Seen before", seen)

# ---------- Tab 2: Results ----------
with tab_results:
    leads = st.session_state.get("leads")
    if not leads:
        st.info("Run the scraper first — results will appear here.")
    else:
        dupes = dedup.duplicate_count(leads)
        with_email = sum(1 for l in leads if l.get("email_best") and l.get("email_status") != "Dead")
        hot = sum(1 for l in leads if l.get("lead_label") == "Hot")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total", len(leads))
        m2.metric("Unique", len(leads) - dupes)
        m3.metric("With email", with_email)
        m4.metric("🔥 Hot", hot)

        f1, f2 = st.columns([2, 1])
        labels = f1.multiselect("Filter by label", ["Hot", "Warm", "Cold"], default=["Hot", "Warm", "Cold"])
        hide_dupes = f2.checkbox("Hide duplicates / seen-before", value=True)

        rows = []
        for l in leads:
            if l.get("lead_label") not in labels:
                continue
            if hide_dupes and (l.get("is_duplicate") or l.get("seen_before")):
                continue
            socials = l.get("socials") or {}
            rows.append({
                "Score": l.get("lead_score"),
                "Label": _label_icon(l),
                "Business": l.get("name"),
                "Phone": l.get("phone"),
                "Website": l.get("website"),
                "Best email": l.get("email_best"),
                "Email": _email_icon(l),
                "Reviews": l.get("reviews"),
                "Rating": l.get("rating"),
                "City": l.get("city"),
                "Facebook": socials.get("facebook"),
                "Instagram": socials.get("instagram"),
                "Maps": l.get("google_maps_url"),
                "Why": l.get("score_reasons"),
                "Dup": l.get("is_duplicate"),
                "Seen": l.get("seen_before"),
            })

        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True,
            column_config={
                "Website": st.column_config.LinkColumn("Website"),
                "Facebook": st.column_config.LinkColumn("Facebook"),
                "Instagram": st.column_config.LinkColumn("Instagram"),
                "Maps": st.column_config.LinkColumn("Maps"),
            },
        )

# ---------- Tab 3: Export ----------
with tab_export:
    leads = st.session_state.get("leads")
    if not leads:
        st.info("Nothing to export yet.")
    else:
        st.subheader("⬇️ Download")
        instantly = exporter.to_instantly_csv(leads)
        calllist = exporter.to_call_list_csv(leads)
        full = exporter.to_full_csv(leads)

        d1, d2, d3 = st.columns(3)
        if d1.download_button("Instantly-ready CSV", instantly, file_name="instantly_leads.csv",
                              mime="text/csv", use_container_width=True):
            history.save_export("instantly_leads.csv", instantly)
        if d2.download_button("Call list CSV", calllist, file_name="call_list.csv",
                              mime="text/csv", use_container_width=True):
            history.save_export("call_list.csv", calllist)
        if d3.download_button("Full data CSV", full, file_name="all_leads.csv",
                              mime="text/csv", use_container_width=True):
            history.save_export("all_leads.csv", full)

    st.divider()
    st.subheader("🗂️ Session history")
    sessions = history.list_sessions()
    if not sessions:
        st.caption("No past sessions yet.")
    else:
        for s in sessions:
            meta = s.get("meta", {})
            line = f"**{s['session_id']}** · {s['modified']} · {s.get('rows', '?')} leads"
            if meta.get("niche"):
                line += f" · _{meta['niche']}_"
            cols = st.columns([4, 1])
            cols[0].markdown(line)
            cols[1].download_button("⬇️ CSV", history.read_session_csv(s["path"]),
                                    file_name=f"{s['session_id']}.csv", mime="text/csv",
                                    key=f"dl_{s['session_id']}")
