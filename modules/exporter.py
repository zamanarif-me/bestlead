"""Export leads to ready-to-use CSVs.

Two deliverables:
  - Instantly CSV : cold-email upload (email, first/last name, company, custom vars).
                    Only rows with a usable, non-dead email are included.
  - Call list CSV : phone outreach, sorted by lead score, only rows with a phone.
"""

from __future__ import annotations

import io

import pandas as pd

# Email statuses safe to load into an email-sending tool.
SENDABLE_STATUSES = {"Valid", "Risky"}


def to_instantly_csv(leads: list[dict], include_risky: bool = True) -> bytes:
    allowed = {"Valid"} | ({"Risky"} if include_risky else set())
    rows = []
    for lead in leads:
        if lead.get("is_duplicate"):
            continue
        email = lead.get("email_best")
        if not email or lead.get("email_status") not in allowed:
            continue

        first, last = _split_name(_contact_name(lead, email))
        rows.append({
            "email": email,
            "first_name": first,
            "last_name": last,
            "company_name": lead.get("name") or "",
            "website": lead.get("website") or "",
            "phone": lead.get("phone") or "",
            "city": lead.get("city") or "",
            "state": lead.get("state") or "",
            "email_type": lead.get("email_best_type") or "",
            "lead_score": lead.get("lead_score", 0),
            "lead_label": lead.get("lead_label", ""),
        })
    return _df_to_csv(rows, columns=[
        "email", "first_name", "last_name", "company_name", "website",
        "phone", "city", "state", "email_type", "lead_score", "lead_label",
    ])


def to_call_list_csv(leads: list[dict]) -> bytes:
    rows = []
    for lead in leads:
        if lead.get("is_duplicate") or not lead.get("phone"):
            continue
        rows.append({
            "company_name": lead.get("name") or "",
            "phone": lead.get("phone") or "",
            "website": lead.get("website") or "",
            "address": lead.get("full_address") or "",
            "city": lead.get("city") or "",
            "rating": lead.get("rating") or "",
            "reviews": lead.get("reviews") or "",
            "lead_score": lead.get("lead_score", 0),
            "lead_label": lead.get("lead_label", ""),
            "why": lead.get("score_reasons") or "",
            "google_maps_url": lead.get("google_maps_url") or "",
        })
    rows.sort(key=lambda r: r["lead_score"], reverse=True)
    return _df_to_csv(rows, columns=[
        "company_name", "phone", "website", "address", "city", "rating",
        "reviews", "lead_score", "lead_label", "why", "google_maps_url",
    ])


def to_full_csv(leads: list[dict]) -> bytes:
    """Everything, for your own records / re-import."""
    rows = []
    for lead in leads:
        row = {k: v for k, v in lead.items() if not k.startswith("_") and k not in ("email_details", "email_contacts", "socials")}
        row["emails"] = ", ".join(lead.get("emails", []))
        row["socials"] = ", ".join(f"{k}:{v}" for k, v in (lead.get("socials") or {}).items())
        rows.append(row)
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")


# ─────────────── helpers ───────────────

def _df_to_csv(rows: list[dict], columns: list[str]) -> bytes:
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def _contact_name(lead: dict, email: str) -> str:
    for c in lead.get("email_contacts", []):
        if c.get("email", "").lower() == email.lower() and c.get("full_name"):
            return c["full_name"]
    return ""


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
