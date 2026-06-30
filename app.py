"""Best Lead — Outscraper Lead Scraper (Digital Zeon).

Pipeline:  scrape -> enrich -> validate -> score -> icebreaker -> dedup
                  -> cross-session history check -> export

Run:  streamlit run app.py
"""

from __future__ import annotations

import os
import time

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from modules import checkpoint, dedup, enrichment, exporter, history, icebreaker, scoring
from modules.scraper import OutscraperClient, ScrapeConfig
from modules.validator import EmailValidator

load_dotenv()  # must run before anything reads os.getenv

st.set_page_config(page_title="Best Lead", page_icon="🎯", layout="wide")
st.title("🎯 Best Lead")
st.caption("Google Maps → enriched, validated, scored home-service leads → Instantly / Call-list CSV")


def _secret(name: str, default: str = "") -> str:
    """Read a config value, preferring Streamlit Cloud secrets, then .env.

    On Streamlit Cloud, put the key under Settings → Secrets as:
        OUTSCRAPER_API_KEY = "sk_..."
    Locally, st.secrets is empty/absent, so we fall back to the .env value.
    """
    try:
        if name in st.secrets:                  # raises if no secrets configured
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def _fmt_elapsed(start: float | None) -> str:
    if not start:
        return ""
    secs = int(time.time() - start)
    mins, secs = divmod(secs, 60)
    return f"{mins}m {secs:02d}s" if mins else f"{secs}s"


# ─────────────────────────── Sidebar ───────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    _key_default = _secret("OUTSCRAPER_API_KEY", "")
    api_key = st.text_input("Outscraper API key", value=_key_default,
                            type="password",
                            help="Auto-loaded from Streamlit Secrets or .env; override here if needed.")
    if _key_default and _key_default != "your_api_key_here":
        st.caption("🔑 Key loaded from secrets/.env.")

    run_mode = st.radio("Run mode", ["Sync", "Async"], horizontal=True,
                        help="Async is recommended for 500+ leads.")
    limit = st.slider("Max results per location", 10, 500, 100, step=10)
    region = st.text_input("Region code", value=os.getenv("DEFAULT_REGION", "US"))
    language = st.text_input("Language", value=os.getenv("DEFAULT_LANGUAGE", "en"))

    st.divider()
    st.subheader("✉️ Email enrichment")
    enrich_mode = st.radio("Mode", ["Basic", "Full"], horizontal=True,
                           help="Basic = Maps emails only. Full = also crawl each domain (more emails + socials).")
    strict_emails = st.checkbox(
        "Only business-domain emails (strict)", value=True,
        help="Keep emails whose domain matches the business website (plus gmail/yahoo "
             "etc. that small businesses really use). Drops foreign/agency/junk emails "
             "scraped from a site's footer or widgets — e.g. copyright@x.com.")

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
def run_pipeline(client: OutscraperClient, leads: list[dict], meta: dict,
                 enrich: str | None = None, levels: tuple[str, ...] | None = None,
                 strict: bool | None = None) -> list[dict]:
    """Enrich → validate → score → … → persist.

    Checkpoint-backed: the scraped leads are saved to disk before any paid work,
    and enrichment/validation consult on-disk caches. So if the network drops or
    the app restarts mid-run, re-running this on the same leads skips everything
    already done (no re-charging) instead of starting over. `enrich`/`levels` let
    a resume reuse the ORIGINAL run's settings rather than the current sidebar.
    """
    if not leads:
        checkpoint.clear_job()
        return leads

    enrich = enrich or enrich_mode
    levels = tuple(levels) if levels else selected_levels()
    strict = strict_emails if strict is None else strict

    # Persist the raw scrape up front — this is the snapshot a resume falls back to.
    checkpoint.update_job(stage="scraped", leads=leads, meta=meta,
                          enrich_mode=enrich, levels=list(levels), strict=strict)

    if enrich == "Full":
        ecache = checkpoint.enrichment_cache()
        bar = st.progress(0.0, text="Enriching emails…")
        enrichment.enrich_leads(client, leads, mode="full", cache=ecache,
                                progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Enriching {d}/{t} domains"))
        bar.empty()
        checkpoint.update_job(stage="enriched", leads=leads)
    else:
        enrichment.enrich_leads(client, leads, mode="basic")

    vcache = checkpoint.validation_cache()
    validator = EmailValidator(client=client if "api" in levels else None,
                               levels=levels, smtp_from=os.getenv("SMTP_FROM_EMAIL"),
                               strict_domain=strict)
    bar = st.progress(0.0, text="Validating emails…")
    validator.validate_leads(leads, cache=vcache,
                             progress=lambda d, t: bar.progress(d / max(t, 1), text=f"Validating {d}/{t} emails"))
    bar.empty()
    checkpoint.update_job(stage="validated", leads=leads)

    scoring.score_leads(leads)
    icebreaker.add_icebreakers(leads)
    dedup.deduplicate(leads)
    history.flag_seen_before(leads)          # compare to PAST sessions
    session = history.save_session(leads, meta)   # persist + update index
    st.session_state["last_session"] = session
    checkpoint.clear_job()                    # run fully complete — nothing to resume
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
tab_run, tab_results, tab_export, tab_history = st.tabs(
    ["🚀 Run Scraper", "📊 Results", "💾 Export", "🗂️ History"])

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
            # Full enrichment is handled by enrichment.enrich_leads() via the
            # /emails-and-contacts crawl (which also returns socials). Do NOT also
            # turn on the search-side `extract_contacts` enrichment, or Outscraper
            # crawls — and bills for — the SAME domains twice. Keep the search lean.
            cfg = ScrapeConfig(niche=niche, locations=locations, limit_per_query=limit,
                               language=language, region=region or None,
                               drop_duplicates=drop_dupes_api, extract_contacts=False)
            meta = {"niche": niche, "locations": locations, "mode": enrich_mode, "run_mode": run_mode}

            # Record the job up front so a drop during scraping can still resume.
            checkpoint.save_job({"job_id": None, "stage": "submitted", "meta": meta,
                                 "enrich_mode": enrich_mode, "levels": list(selected_levels()),
                                 "strict": strict_emails, "run_mode": run_mode, "leads": None})

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
                st.session_state["job_started_at"] = time.time()
                st.session_state.pop("leads", None)
                checkpoint.update_job(job_id=request_id)   # persist handle for resume
                st.info(f"Async job started. Job ID: `{request_id}`")
        except Exception as e:
            st.error(f"❌ {e}")

    # ── Resume an interrupted run (wifi drop / refresh / app restart) ──
    resume = checkpoint.load_job()
    if resume and not st.session_state.get("leads") and not st.session_state.get("request_id"):
        stage = resume.get("stage")
        with st.container(border=True):
            if stage == "submitted" and resume.get("job_id"):
                st.warning("🔁 Unfinished job found — still scraping on Outscraper's side.")
                st.code(resume["job_id"], language=None)
                rc1, rc2 = st.columns(2)
                if rc1.button("▶️ Resume this job", use_container_width=True):
                    st.session_state["request_id"] = resume["job_id"]
                    st.session_state["pending_meta"] = resume.get("meta", {})
                    st.session_state["job_started_at"] = time.time()
                    st.rerun()
                if rc2.button("🗑️ Discard", use_container_width=True):
                    checkpoint.clear_job()
                    st.rerun()
            elif resume.get("leads"):
                n = len(resume["leads"])
                st.warning(f"🔁 A previous run was interrupted at **{stage}** with **{n} leads**. "
                           "Resume to finish — already-validated emails and crawled domains are "
                           "skipped, so you're not re-charged.")
                rc1, rc2 = st.columns(2)
                if rc1.button("▶️ Resume processing", type="primary", use_container_width=True):
                    try:
                        client = OutscraperClient(api_key=api_key)
                        leads = run_pipeline(client, resume["leads"], resume.get("meta", {}),
                                             enrich=resume.get("enrich_mode"),
                                             levels=tuple(resume.get("levels") or ()),
                                             strict=resume.get("strict"))
                        st.session_state["leads"] = leads
                        st.success(f"Done — {len(leads)} leads.")
                    except Exception as e:
                        st.error(f"❌ {e}")
                if rc2.button("🗑️ Discard", use_container_width=True):
                    checkpoint.clear_job()
                    st.rerun()

    # Async job tracker
    if st.session_state.get("request_id") and "leads" not in st.session_state:
        started = st.session_state.get("job_started_at")
        elapsed = _fmt_elapsed(started)

        # Live "it's working" sign: animated status + running clock.
        st.status(f"⏳ Async job running{(' · ' + elapsed) if elapsed else ''}…",
                  state="running", expanded=False)
        st.code(st.session_state["request_id"], language=None)

        bcols = st.columns([1, 1])
        check = bcols[0].button("🔄 Check job status", use_container_width=True)
        auto = bcols[1].checkbox("🔁 Auto-refresh (10s)",
                                 value=st.session_state.get("auto_poll", False),
                                 help="Keeps checking on its own until the job finishes.")
        st.session_state["auto_poll"] = auto

        if check or auto:
            try:
                client = OutscraperClient(api_key=api_key)
                with st.spinner("Checking Outscraper…"):
                    res = client.poll_async(st.session_state["request_id"])
                if res["status"] == "Success":
                    job = checkpoint.load_job() or {}   # use the run's ORIGINAL settings
                    leads = run_pipeline(client, res["leads"], st.session_state.get("pending_meta", {}),
                                         enrich=job.get("enrich_mode"),
                                         levels=tuple(job.get("levels") or ()),
                                         strict=job.get("strict"))
                    st.session_state["leads"] = leads
                    st.session_state.pop("request_id", None)
                    st.session_state.pop("auto_poll", None)
                    st.success(f"Done — {len(leads)} leads in {elapsed or 'a moment'}.")
                elif res["status"] == "Error":
                    st.error("Outscraper reported an error for this job.")
                    st.session_state.pop("request_id", None)
                    st.session_state.pop("auto_poll", None)
                    checkpoint.clear_job()
                else:
                    if auto:
                        # Visible animated wait, then re-poll automatically.
                        with st.spinner(f"Still working — re-checking in 10s (running {elapsed})…"):
                            time.sleep(10)
                        st.rerun()
                    else:
                        st.info("Still pending… click again, or enable Auto-refresh.")
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
        chosen = st.multiselect(
            "Include labels", ["Hot", "Warm", "Cold"], default=["Hot", "Warm", "Cold"],
            help="Export only the lead temperatures you pick — e.g. just Hot + Warm.")
        export_leads = [l for l in leads if l.get("lead_label") in chosen] if chosen else []
        st.caption(f"{len(export_leads)} of {len(leads)} leads match the selected labels.")

        instantly = exporter.to_instantly_csv(export_leads)
        calllist = exporter.to_call_list_csv(export_leads)
        full = exporter.to_full_csv(export_leads)

        d1, d2, d3 = st.columns(3)
        if d1.download_button("Instantly-ready CSV", instantly, file_name="instantly_leads.csv",
                              mime="text/csv", use_container_width=True,
                              disabled=not export_leads):
            history.save_export("instantly_leads.csv", instantly)
        if d2.download_button("Call list CSV", calllist, file_name="call_list.csv",
                              mime="text/csv", use_container_width=True,
                              disabled=not export_leads):
            history.save_export("call_list.csv", calllist)
        if d3.download_button("Full data CSV", full, file_name="all_leads.csv",
                              mime="text/csv", use_container_width=True,
                              disabled=not export_leads):
            history.save_export("all_leads.csv", full)


# ---------- Tab 4: History ----------
with tab_history:
    st.subheader("🗂️ Session history")
    st.caption("Every run is saved here automatically. Download any past run's full "
               "CSV, even after closing the app.")
    st.caption("⚠️ Stored on local disk (./data). On ephemeral hosts (Streamlit Cloud, "
               "most containers) this resets on restart — see README → Deployment for "
               "persistent storage.")

    sessions = history.list_sessions()
    if not sessions:
        st.info("No past sessions yet — run the scraper and they'll appear here.")
    else:
        st.caption(f"{len(sessions)} saved session(s).")
        for s in sessions:
            meta = s.get("meta", {})
            bits = [f"**{s['session_id']}**", s["modified"], f"{s.get('rows', '?')} leads"]
            if meta.get("niche"):
                bits.append(f"_{meta['niche']}_")
            if meta.get("locations"):
                locs = meta["locations"]
                bits.append("📍 " + (", ".join(locs[:2]) + (f" +{len(locs) - 2}" if len(locs) > 2 else "")))
            if meta.get("mode"):
                bits.append(f"✉️ {meta['mode']}")
            cols = st.columns([5, 1])
            cols[0].markdown(" · ".join(bits))
            cols[1].download_button("⬇️ CSV", history.read_session_csv(s["path"]),
                                    file_name=f"{s['session_id']}.csv", mime="text/csv",
                                    key=f"dl_{s['session_id']}")
