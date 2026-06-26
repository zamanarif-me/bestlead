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

import dns.resolver

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

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
                 smtp_from: str | None = None, max_workers: int = 8):
        self.client = client                       # OutscraperClient (for API level)
        self.levels = set(levels)
        self.smtp_from = smtp_from or os.getenv("SMTP_FROM_EMAIL", "verify@example.com")
        self.smtp_helo = os.getenv("SMTP_FROM_DOMAIN", "example.com")
        self.max_workers = max_workers
        self._mx_cache: dict[str, list[str]] = {}

    # ─────────────── public API ───────────────

    def validate_leads(self, leads: list[dict], progress=None) -> list[dict]:
        """Validate every email on every lead, then pick the best one."""
        all_emails = sorted({e.lower() for lead in leads for e in lead.get("emails", [])})

        results: dict[str, dict] = {}
        total = len(all_emails)
        done = 0

        # SMTP is I/O-bound -> thread it. Syntax/MX/API-only stays light.
        if "smtp" in self.levels and total:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {pool.submit(self.validate_email, e): e for e in all_emails}
                for fut in as_completed(futures):
                    email = futures[fut]
                    results[email] = fut.result()
                    done += 1
                    if progress:
                        progress(done, total)
        else:
            for email in all_emails:
                results[email] = self.validate_email(email)
                done += 1
                if progress:
                    progress(done, total)

        for lead in leads:
            details = [results[e.lower()] for e in lead.get("emails", []) if e.lower() in results]
            lead["email_details"] = details
            best = _pick_best(details)
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

def classify_type(email: str) -> str:
    local, _, domain = email.partition("@")
    if domain in FREE_PROVIDERS:
        return "Personal"
    base = local.split("+")[0]
    if base in GENERIC_PREFIXES:
        return "Generic"
    return "Direct"


def _pick_best(details: list[dict]) -> dict | None:
    """Prefer deliverable Direct > Generic > anything; Dead emails last."""
    if not details:
        return None

    type_rank = {"Direct": 3, "Generic": 2, "Personal": 1, "Dead": 0, "Unknown": 1}
    status_rank = {"Valid": 3, "Risky": 2, "Unknown": 1, "Dead": 0}

    def key(d):
        return (status_rank.get(d["status"], 0), type_rank.get(d["type"], 0))

    return max(details, key=key)
