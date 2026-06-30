"""4-level dead-email detection + email-type labeling.

Levels (each more expensive / more accurate than the last):
  1. SYNTAX  — regex shape check.            (instant)
  2. MX      — does the domain accept mail?   (DNS lookup, cached per domain)
  3. SMTP    — RCPT TO probe on the MX host.  (slow, often blocked/catch-all)
  4. API     — Outscraper email validator.    (paid, most reliable)

Type labels:
  Direct   — looks like a person at a business domain   (john@acme.com)
  Generic  — role inbox                                 (info@, sales@, ...)
  Personal — free mail provider                         (gmail/yahoo/...)
  Dead     — failed validation
"""

from __future__ import annotations

import os
import re
import smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import dns.resolver

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Domains that are never a business's real contact address — they get scraped out
# of a site's footer links, embedded widgets, analytics, registrar/CDN, or social
# buttons. Emails on these are always dropped (e.g. copyright@x.com, *@sentry.io).
JUNK_EMAIL_DOMAINS = {
    "x.com", "twitter.com", "facebook.com", "fb.com", "instagram.com",
    "linkedin.com", "youtube.com", "pinterest.com", "tiktok.com", "wa.me",
    "sentry.io", "sentry-next.wixpress.com", "wix.com", "wixpress.com",
    "squarespace.com", "godaddy.com", "shopify.com", "cloudflare.com",
    "google.com", "gstatic.com", "googleapis.com", "schema.org", "w3.org",
    "example.com", "domain.com", "email.com", "sentry.wixpress.com",
    "verizonwireless.com", "wordpress.com", "wordpress.org",
}

GENERIC_PREFIXES = {
    "info", "contact", "sales", "support", "admin", "hello", "office", "team",
    "help", "service", "services", "enquiries", "inquiries", "mail", "marketing",
    "hr", "jobs", "careers", "billing", "accounts", "accounting", "noreply",
    "no-reply", "donotreply", "webmaster", "post", "general", "reception",
}

FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "gmx.com", "mail.com", "protonmail.com", "proton.me",
    "yandex.com", "live.com", "msn.com", "me.com", "comcast.net",
}


class EmailValidator:
    """Stateful validator. Holds DNS/SMTP caches so repeated domains are cheap."""

    def __init__(self, client=None, levels: tuple[str, ...] = ("syntax", "mx"),
                 smtp_from: str | None = None, max_workers: int = 8,
                 strict_domain: bool = True):
        self.client = client                       # OutscraperClient (for API level)
        self.levels = set(levels)
        self.smtp_from = smtp_from or os.getenv("SMTP_FROM_EMAIL", "verify@example.com")
        self.smtp_helo = os.getenv("SMTP_FROM_DOMAIN", "example.com")
        self.max_workers = max_workers
        self.strict_domain = strict_domain         # drop emails not matching the website
        self._mx_cache: dict[str, list[str]] = {}

    # ─────────────── public API ───────────────

    def validate_leads(self, leads: list[dict], progress=None, cache=None) -> list[dict]:
        """Validate every email on every lead, then pick the best one.

        `cache` (optional) is a checkpoint.JsonCache of {email: result}. Emails
        already in it are reused — so after a wifi drop / restart the resumed run
        skips everything already done and never re-charges the paid L4 validator.
        Only confident results are cached (see `_cacheable`); a "Dead" caused by a
        DNS/SMTP/API blip is NOT cached, so it is safely re-checked on resume.
        """
        # Drop junk/foreign emails up front (per lead, against its own website),
        # BEFORE validating — so we never pay L4 credits to validate garbage.
        for lead in leads:
            site = _site_domain(lead.get("website"))
            lead["emails"] = [e for e in (lead.get("emails") or [])
                              if _trusted_email(e, site, self.strict_domain)]

        all_emails = sorted({e.lower() for lead in leads for e in lead.get("emails", [])})

        results: dict[str, dict] = {}
        total = len(all_emails)

        # Seed from cache; only the rest needs (re)validation.
        todo = []
        for email in all_emails:
            if cache is not None and email in cache:
                results[email] = cache.get(email)
            else:
                todo.append(email)
        done = len(results)
        if progress:
            progress(done, total)

        def record(email: str, res: dict) -> None:
            results[email] = res
            if cache is not None and _cacheable(res):
                cache.put(email, res)

        # SMTP is I/O-bound -> thread it. Syntax/MX/API-only stays light.
        if "smtp" in self.levels and todo:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {pool.submit(self.validate_email, e): e for e in todo}
                for fut in as_completed(futures):
                    email = futures[fut]
                    record(email, fut.result())
                    done += 1
                    if progress:
                        progress(done, total)
        else:
            for email in todo:
                record(email, self.validate_email(email))
                done += 1
                if progress:
                    progress(done, total)

        if cache is not None:
            cache.flush()

        for lead in leads:
            details = [results[e.lower()] for e in lead.get("emails", []) if e.lower() in results]
            lead["email_details"] = details
            best = _pick_best(details, _site_domain(lead.get("website")))
            lead["email_best"] = best.get("email") if best else None
            lead["email_best_type"] = best.get("type") if best else None
            lead["email_status"] = best.get("status") if best else "No email"
        return leads

    def validate_email(self, email: str) -> dict:
        email = (email or "").strip().lower()
        record = {
            "email": email, "syntax": None, "mx": None,
            "smtp": None, "api": None, "status": "Unknown", "type": "Unknown",
        }

        # Level 1 — syntax
        record["syntax"] = bool(_EMAIL_RE.match(email))
        if not record["syntax"]:
            record["status"], record["type"] = "Dead", "Dead"
            return record

        domain = email.split("@", 1)[1]

        # Level 2 — MX
        if "mx" in self.levels or "smtp" in self.levels:
            mx_hosts = self._mx(domain)
            record["mx"] = bool(mx_hosts)
            if not mx_hosts:
                record["status"], record["type"] = "Dead", "Dead"
                return record
        else:
            mx_hosts = []

        # Level 3 — SMTP RCPT probe
        if "smtp" in self.levels:
            record["smtp"] = self._smtp(email, mx_hosts)
            if record["smtp"] is False:
                record["status"], record["type"] = "Dead", "Dead"
                return record

        # Level 4 — Outscraper API validator
        if "api" in self.levels and self.client is not None:
            api_ok = self._api(email)
            record["api"] = api_ok
            if api_ok is False:
                record["status"], record["type"] = "Dead", "Dead"
                return record

        # Final status: "Valid" if any positive signal, else "Risky".
        positive = record["smtp"] is True or record["api"] is True
        record["status"] = "Valid" if positive else "Risky"
        record["type"] = classify_type(email)
        return record

    # ─────────────── levels ───────────────

    def _mx(self, domain: str) -> list[str]:
        if domain in self._mx_cache:
            return self._mx_cache[domain]
        hosts: list[str] = []
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5)
            ranked = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
            hosts = [h for _, h in ranked]
        except Exception:
            hosts = []
        self._mx_cache[domain] = hosts
        return hosts

    def _smtp(self, email: str, mx_hosts: list[str]) -> bool | None:
        """True = accepted, False = rejected, None = inconclusive (catch-all/blocked)."""
        for host in mx_hosts[:2]:
            try:
                server = smtplib.SMTP(timeout=8)
                server.connect(host, 25)
                server.helo(self.smtp_helo)
                server.mail(self.smtp_from)
                code, _ = server.rcpt(email)
                server.quit()
                if code in (250, 251):
                    return True
                if code in (550, 551, 553, 554):
                    return False
            except Exception:
                continue
        return None

    def _api(self, email: str) -> bool | None:
        """Outscraper email-validator via REST. True/False/None (unknown)."""
        try:
            item = self.client.validate_email_api(email)
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        status = str(item.get("status", "")).lower()
        # Order matters: "invalid" contains the substring "valid".
        if any(k in status for k in ("invalid", "undeliverable", "disabled", "unknown")):
            return False if "unknown" not in status else None
        if any(k in status for k in ("valid", "deliverable", "ok", "safe")):
            return True
        return None


# ─────────────── helpers ───────────────

def _cacheable(res: dict) -> bool:
    """Only persist confident verdicts to the resume cache.

    Valid/Risky are safe to remember. A syntax-failure Dead is deterministic, so
    cache it too. But a Dead/None from MX/SMTP/API could be a transient network
    failure (the very thing we're guarding against) — never cache that, so it is
    re-checked cleanly on the next run.
    """
    status = res.get("status")
    if status in ("Valid", "Risky"):
        return True
    if status == "Dead" and res.get("syntax") is False:
        return True
    return False


def classify_type(email: str) -> str:
    local, _, domain = email.partition("@")
    if domain in FREE_PROVIDERS:
        return "Personal"
    base = local.split("+")[0]
    if base in GENERIC_PREFIXES:
        return "Generic"
    return "Direct"


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()


def _site_domain(website: str | None) -> str:
    """Bare host of a business website, e.g. 'https://www.Acme.com/x' -> 'acme.com'."""
    if not website:
        return ""
    url = website.strip()
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_domain(email_dom: str, site_dom: str) -> bool:
    """True if the email lives on the website's domain (or a sub/parent of it)."""
    if not email_dom or not site_dom:
        return False
    return (email_dom == site_dom
            or email_dom.endswith("." + site_dom)
            or site_dom.endswith("." + email_dom))


def _trusted_email(email: str | None, site_domain: str, strict: bool) -> bool:
    """Decide whether an email is worth keeping.

    Always drops known-junk domains (social/CDN/registrar footer scrapes). In
    strict mode additionally keeps ONLY emails on the business's own domain or a
    free provider (gmail/yahoo — small businesses really use those), dropping
    foreign corporate emails accidentally scraped from the page.
    """
    dom = _email_domain(email)
    if not dom or dom in JUNK_EMAIL_DOMAINS:
        return False
    if not strict:
        return True
    if dom in FREE_PROVIDERS:
        return True
    return _same_domain(dom, site_domain)


def _pick_best(details: list[dict], site_domain: str | None = None) -> dict | None:
    """Pick the best email: on-domain first, then deliverable, then Direct>Generic.

    On-domain (matches the business website) is the strongest signal that the
    address truly belongs to this business, so it outranks everything else.
    """
    if not details:
        return None

    type_rank = {"Direct": 3, "Generic": 2, "Personal": 1, "Dead": 0, "Unknown": 1}
    status_rank = {"Valid": 3, "Risky": 2, "Unknown": 1, "Dead": 0}

    def key(d):
        on_domain = 1 if _same_domain(_email_domain(d.get("email")), site_domain or "") else 0
        return (on_domain, status_rank.get(d["status"], 0), type_rank.get(d["type"], 0))

    return max(details, key=key)
