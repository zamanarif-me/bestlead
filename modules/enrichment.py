"""Email / contact enrichment.

Two modes:
  - "basic": keep only the emails Outscraper already returned with each place.
  - "full" : additionally call Outscraper's emails-and-contacts service on each
             unique business domain to pull extra emails + social profiles.
"""

from __future__ import annotations

from urllib.parse import urlparse


def enrich_leads(client, leads: list[dict], mode: str = "basic", progress=None) -> list[dict]:
    """Mutate and return `leads` with enriched email/social data.

    `client` is an `OutscraperClient`. `progress` is an optional callable
    `progress(done, total)` for the Streamlit progress bar.
    """
    if mode == "basic":
        return leads

    domains = _unique_domains(leads)
    if not domains:
        return leads

    contact_map = _fetch_contacts(client, domains, progress)

    for lead in leads:
        domain = _domain_of(lead.get("website"))
        extra = contact_map.get(domain)
        if not extra:
            continue

        merged = {e.lower() for e in lead.get("emails", [])}
        for raw_email in _iter_emails(extra):
            if raw_email:
                merged.add(raw_email.lower())
        lead["emails"] = sorted(merged)

        socials = lead.get("socials", {})
        for key in ("facebook", "instagram", "linkedin", "twitter", "youtube"):
            if extra.get(key):
                socials[key] = extra[key]
        lead["socials"] = socials

    return leads


# ───────────────────────── internals ─────────────────────────

def _fetch_contacts(client, domains: list[str], progress) -> dict[str, dict]:
    """Call Outscraper emails-and-contacts, batched, return {domain: record}."""
    out: dict[str, dict] = {}
    batch_size = 25
    total = len(domains)
    done = 0

    for start in range(0, total, batch_size):
        batch = domains[start:start + batch_size]
        try:
            results = client.emails_and_contacts(batch)
        except Exception:
            results = []

        for item in _flatten(results):
            domain = _domain_of(item.get("query") or item.get("domain"))
            if domain:
                out[domain] = item

        done += len(batch)
        if progress:
            progress(done, total)

    return out


def _iter_emails(record: dict):
    """Outscraper returns emails either as a list of dicts or flat email_N keys."""
    emails = record.get("emails")
    if isinstance(emails, list):
        for e in emails:
            yield e.get("value") if isinstance(e, dict) else e
    for i in range(1, 11):
        v = record.get(f"email_{i}")
        if v:
            yield v


def _unique_domains(leads: list[dict]) -> list[str]:
    seen: list[str] = []
    known = set()
    for lead in leads:
        d = _domain_of(lead.get("website"))
        if d and d not in known:
            known.add(d)
            seen.append(d)
    return seen


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or None


def _flatten(results):
    rows = []
    if not results:
        return rows
    for group in results:
        if isinstance(group, list):
            rows.extend(g for g in group if isinstance(g, dict))
        elif isinstance(group, dict):
            rows.append(group)
    return rows
