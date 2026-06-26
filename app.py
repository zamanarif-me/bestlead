"""Outscraper Lead Scraper — Streamlit app.

Pipeline:  scrape -> enrich -> validate -> score -> dedup -> export

Run:  streamlit run app.py
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from modules import dedup, enrichment, exporter, scoring
from modules.scraper import OutscraperClient, ScrapeConfig
from modules.validator import EmailValidator

load_dotenv()

st.set_page_config(page_title="Best Lead", page_icon="🎯", layout="wide")
st.title("🎯 Best Lead")
st.caption("Niche + location → scraped, enriched, validated, scored leads → Instantly / Call-list CSV")


# ─────────────────────────── Sidebar ───────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    api_key = st.text_input(
        "Outscraper API key",
        value=os.getenv("OUTSCRAPER_API_KEY", ""),
        type="password",
        help="Loaded from .env; override here if needed.",
    )

    run_mode = st.radio("Run mode", ["Sync", "Async"], horizontal=True,
                        help="Async is recommended for big jobs (many locations / high limit).")
    limit = st.slider("Max results per location", 10, 500, 100, step=10)
    region = st.text_input("Region code", value=os.getenv("DEFAULT_REGION", "US"))
    language = st.text_input("Language", value=os.getenv("DEFAULT_LANGUAGE", "en"))

    st.divider()
    st.subheader("✉️ Email enrichment")
    enrich_mode = st.radio("Mode", ["Basic", "Full"], horizontal=True,
                           help="Basic = emails from Maps only. Full = also crawl each domain (slower, more emails).")

    st.subheader("🛡️ Dead-email detection")
    lvl_syntax = st.checkbox("L1 · Syntax", value=True, disabled=True)
    lvl_mx = st.checkbox("L2 · MX (DNS)", value=True)
    lvl_smtp = st.checkbox("L3 · SMTP probe", value=False, help="Most accurate free check, but slow & sometimes blocked.")
    lvl_api = st.checkbox("L4 · Outscraper API", value=False, help="Paid, most reliable.")

    st.divider()
    drop_dupes_api = st.checkbox("Ask Outscraper to drop duplicates", value=True)


# ─────────────────────────── Inputs ───────────────────────────
col1, col2 = st.columns(2)
with col1:
    niche = st.text_input("Service / niche keyword", placeholder="e.g. dentist, plumber, gym, law firm")
with col2:
    st.write("**Locations**")
    uploaded = st.file_uploader("Bulk locations CSV (optional)", type=["csv"],
                                help="A column named 'location' (or the first column) is used.")

locations_text = st.text_area(
    "Locations (one per line)",
    placeholder="New York, NY\n90210\nAustin, TX\nUnited Kingdom",
    height=120,
)


def collect_locations() -> list[str]:
    locs: list[str] = []
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            col = "location" if "location" in df.columns else df.columns[0]
            locs += [str(v).strip() for v in df[col].dropna().tolist()]
        except Exception as e:
            st.warning(f"Couldn't read CSV: {e}")
    locs += [l.strip() for l in locations_text.splitlines() if l.strip()]
    # de-dupe while preserving order
    seen, out = set(), []
    for l in locs:
        if l.lower() not in seen:
            seen.add(l.lower())
            out.append(l)
    return out


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
def run_pipeline(client: OutscraperClient, leads: list[dict]) -> list[dict]:
    """enrich -> validate -> score -> dedup. Shared by sync & async paths."""
    if not leads:
        return leads

    # Enrich
    if enrich_mode == "Full":
        bar = st.progress(0.0, text="Enriching emails…")
        enrichment.enrich_leads(
            client, leads, mode="full",
            progress=lambda d, t: bar.progress(d / t, text=f"Enriching {d}/{t} domains"),
        )
        bar.empty()
    else:
        enrichment.enrich_leads(client, leads, mode="basic")

    # Validate
    validator = EmailValidator(
        client=client if lvl_api else None,
        levels=selected_levels(),
        smtp_from=os.getenv("SMTP_FROM_EMAIL"),
    )
    bar = st.progress(0.0, text="Validating emails…")
    validator.validate_leads(
        leads,
        progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Validating {d}/{t} emails"),
    )
    bar.empty()

    # Score + dedup
    scoring.score_leads(leads)
    dedup.deduplicate(leads)
    return leads


def show_results(leads: list[dict]):
    total = len(leads)
    dupes = dedup.duplicate_count(leads)
    with_email = sum(1 for l in leads if l.get("email_best") and l.get("email_status") != "Dead")
    hot = sum(1 for l in leads if l.get("lead_label") == "Hot")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total leads", total)
    m2.metric("Unique", total - dupes)
    m3.metric("With usable email", with_email)
    m4.metric("🔥 Hot", hot)

    # Display table
    view = pd.DataFrame([{
        "Score": l.get("lead_score"),
        "Label": l.get("lead_label"),
        "Name": l.get("name"),
        "Phone": l.get("phone"),
        "Website": l.get("website"),
        "Best email": l.get("email_best"),
        "Email type": l.get("email_best_type"),
        "Email status": l.get("email_status"),
        "Reviews": l.get("reviews"),
        "Rating": l.get("rating"),
        "City": l.get("city"),
        "Duplicate": l.get("is_duplicate"),
        "Why": l.get("score_reasons"),
    } for l in leads])
    st.dataframe(view, use_container_width=True, hide_index=True)

    # Downloads
    st.subheader("⬇️ Export")
    d1, d2, d3 = st.columns(3)
    d1.download_button("Instantly-ready CSV", exporter.to_instantly_csv(leads),
                       file_name="instantly_leads.csv", mime="text/csv", use_container_width=True)
    d2.download_button("Call list CSV", exporter.to_call_list_csv(leads),
                       file_name="call_list.csv", mime="text/csv", use_container_width=True)
    d3.download_button("Full data CSV", exporter.to_full_csv(leads),
                       file_name="all_leads.csv", mime="text/csv", use_container_width=True)


# ─────────────────────────── Actions ───────────────────────────
st.divider()
go = st.button("🚀 Scrape leads", type="primary", use_container_width=True)

if go:
    try:
        client = OutscraperClient(api_key=api_key)
        locations = collect_locations()
        cfg = ScrapeConfig(
            niche=niche, locations=locations, limit_per_query=limit,
            language=language, region=region or None,
            drop_duplicates=drop_dupes_api, extract_contacts=(enrich_mode == "Full"),
        )

        if run_mode == "Sync":
            with st.spinner(f"Scraping {len(locations)} location(s)…"):
                leads = client.search_sync(cfg)
            leads = run_pipeline(client, leads)
            st.session_state["leads"] = leads
            st.success(f"Done — {len(leads)} leads scraped.")
        else:
            request_id = client.search_async(cfg)
            st.session_state["request_id"] = request_id
            st.session_state.pop("leads", None)
            st.info(f"Async job started. Request id: `{request_id}`. Poll below.")
    except Exception as e:
        st.error(f"❌ {e}")


# Async polling UI
if st.session_state.get("request_id") and "leads" not in st.session_state:
    st.warning("⏳ Async job in progress.")
    if st.button("🔄 Check job status"):
        try:
            client = OutscraperClient(api_key=api_key)
            res = client.poll_async(st.session_state["request_id"])
            if res["status"] == "Success":
                leads = run_pipeline(client, res["leads"])
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


if st.session_state.get("leads"):
    show_results(st.session_state["leads"])
