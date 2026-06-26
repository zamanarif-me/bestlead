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
Edit `.env` and set your key:
```
OUTSCRAPER_API_KEY=sk_...
```

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
  exporter.py           # Instantly / Call-list / Full CSV export
.env                    # API key + SMTP-probe identity
requirements.txt
```

## Notes
- **SMTP probe (L3)** is the most accurate free check but is slow and many
  providers block port 25 or return catch-all answers → results can be `Risky`
  (inconclusive) rather than Valid/Dead. Enable it only when accuracy matters.
- **Full enrichment** and the **API validator (L4)** consume Outscraper credits.
- Lead-scoring weights live in `modules/scoring.py → DEFAULT_WEIGHTS`.
