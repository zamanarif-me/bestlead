# 🎯 Best Lead

A Streamlit app that scrapes business leads from Google Maps via the
[Outscraper API](https://outscraper.com/), enriches & validates their emails,
scores them for outreach, removes duplicates, and exports ready-to-use CSVs.

## Features
- Any **service / niche** keyword + any **location** (city / ZIP / state / country)
- **Bulk locations** via CSV upload (column `location`, or first column)
- **Sync** (small jobs) and **Async** (big jobs, poll for results) modes
- **Email enrichment** — Basic (Maps only) vs Full (crawl each domain)
- **4-level dead-email detection** — Syntax → MX → SMTP → Outscraper API
- **Email type labels** — Direct / Generic / Personal / Dead
- **Lead scoring** — no website, few reviews, low rating, reachability…
- **Duplicate detection** — place_id → phone → name+address
- **Exports** — Instantly-ready CSV, Call-list CSV, Full data CSV

## Setup
```bash
pip install -r requirements.txt
```
Provide your Outscraper key one of three ways (checked in this order):

1. **Streamlit Secrets** (recommended for Streamlit Cloud) — Manage app →
   Settings → Secrets, paste:
   ```toml
   OUTSCRAPER_API_KEY = "sk_..."
   ```
   Locally, copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`.
2. **`.env`** (copy `.env.example` → `.env`):
   ```
   OUTSCRAPER_API_KEY=sk_...
   ```
3. **Paste it in the sidebar** at runtime (overrides the above).

## Run
```bash
streamlit run app.py
```

## File structure
```
app.py                  # Streamlit UI + pipeline orchestration
modules/
  scraper.py            # Outscraper wrapper (sync/async/bulk) + normalization
  enrichment.py         # Basic vs Full email/contact enrichment
  validator.py          # 4-level validation + email-type labeling
  scoring.py            # Lead scoring (weights tunable)
  dedup.py              # Duplicate detection
  history.py            # Persistent session history + cross-session dedup
  checkpoint.py         # Crash/disconnect resume: job handle + per-key caches
  icebreaker.py         # Template cold-email first lines
  exporter.py           # Instantly / Call-list / Full CSV export
tests/                  # Network-free unit tests (pytest)
.env.example            # Copy to .env, then fill in your key + SMTP identity
requirements.txt
requirements-dev.txt    # adds pytest
```

## Notes
- **SMTP probe (L3)** is the most accurate free check but is slow and many
  providers block port 25 or return catch-all answers → results can be `Risky`
  (inconclusive) rather than Valid/Dead. Enable it only when accuracy matters.
- **Full enrichment** and the **API validator (L4)** consume Outscraper credits.
  Full enrichment is crawled **once** via `/emails-and-contacts` — the search is
  intentionally **not** also asked to enrich, which would double-bill the domains.
- Lead-scoring weights live in `modules/scoring.py → DEFAULT_WEIGHTS`.
- **Run mode:** internally even "Sync" submits the job to Outscraper as a
  background request and polls for the result, so heavy jobs (Full enrichment,
  large limits, dense cities) don't trip the gateway's ~60s `504` timeout. Use
  **Async** for very large batches so the UI stays responsive.

## Resilience / resume (network drops mid-run)
A run is checkpointed so a wifi drop, browser refresh, or app restart doesn't
cost you the run or re-charge you for work already done:

1. **Job handle** — when an Async job is submitted, its `request_id` + settings
   are saved to `data/jobs/active.json`. After a refresh, the **🔁 Resume this
   job** panel restores it so you can keep polling instead of losing the job.
2. **Scraped leads** — saved to the same job file *before* any paid enrichment/
   validation, so the leads are never lost once scraped.
3. **Per-key caches** — `data/cache/validation.json` (`{email: result}`) and
   `data/cache/enrichment.json` (`{domain: record}`) are written incrementally as
   work completes. On **▶️ Resume processing**, already-validated emails and
   already-crawled domains are **skipped**, so the paid L4 validator and Full
   crawl are **not re-billed**.

Inconclusive results are deliberately *not* cached: a `Dead` verdict caused by a
DNS/SMTP/API blip (rather than a real syntax failure) is re-checked on resume, so
a network glitch never poisons the cache with a false negative.

> Prefer **Async** mode for anything you might need to resume — its job handle is
> what survives a refresh. (Same ephemeral-filesystem caveat as below: this state
> survives a wifi drop, but not a host reboot on Streamlit Cloud.)

## Deployment
History, the cross-session dedup index, and saved export copies are written to a
local `./data/` directory:

```
data/history/_seen_index.json    # {identity_key: session_id}
data/history/session_*.csv       # full snapshot of each run
data/exports/<ts>_<name>.csv     # a copy of every downloaded export
```

> ⚠️ **Ephemeral filesystems wipe this.** On Streamlit Community Cloud and most
> container hosts, the filesystem resets on every restart/redeploy — so session
> history and the "Seen before" cross-session dedup **silently reset**. Index
> writes are atomic (temp file + `os.replace`), which prevents *corruption*, but
> not *loss* on an ephemeral host.
>
> For durable history, mount a persistent volume at `./data`, or back the index
> and snapshots with external storage (S3, a database, etc.). Running locally or
> on a host with a persistent disk needs no changes.

## Testing
```bash
pip install -r requirements-dev.txt
pytest                 # from the project root
```
Tests cover the pure pipeline functions (normalization, dedup/identity, email
classification, scoring, CSV export filtering, atomic writes) and make no
network calls.
