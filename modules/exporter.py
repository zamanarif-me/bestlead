"""Export leads to ready-to-use CSVs.

  - Instantly CSV : cold-email upload. Only rows with a usable, non-dead email.
                    First/Last, Email, Business, Phone, Website, City, State,
                    Score, Email Type, Icebreaker, Facebook, Instagram.
  - Call list CSV : phone outreach, sorted by score, rows with a phone.
                    Business, Phone, City, Score, Has Website, Review Count,
                    Facebook, Notes.
  - Full CSV      : everything, for your own records / re-import.

Rows that are in-batch duplicates OR already seen in a past session are skipped
from the outreach CSVs.
"""

from __future__ import annotations

import io

import pandas as pd

INSTANTLY_COLUMNS = [
    "First Name", "Last Name", "Email", "Business Name", "Phone", "Website",
    "City", "State", "Score", "Email Type", "Icebreaker", "Facebook", "Instagram",
]

CALL_LIST_COLUMNS = [
    "Business Name", "Phone", "City", "Score", "Has Website",
    "Review Count", "Facebook", "Notes",
]


def _exportable(lead: dict) -> bool:
    return not (lead.get("is_duplicate") or lead.get("seen_before"))


def to_instantly_csv(leads: list[dict], include_risky: bool = True) -> bytes:
    allowed = {"Valid"} | ({"Risky"} if include_risky else set())
    rows = []
    for lead in leads:
        if not _exportable(lead):
            continue
        email = lead.get("email_best")
        if not email or lead.get("email_status") not in allowed:
            continue

        first, last = _split_name(_contact_name(lead, email))
        socials = lead.get("socials") or {}
        rows.append({
            "First Name": first,
            "Last Name": last,
            "Email": email,
            "Business Name": lead.get("name") or "",
            "Phone": lead.get("phone") or "",
            "Website": lead.get("website") or "",
            "City": lead.get("city") or "",
            "State": lead.get("state") or "",
            "Score": lead.get("lead_score", 0),
            "Email Type": lead.get("email_best_type") or "",
            "Icebreaker": lead.get("icebreaker") or "",
            "Facebook": socials.get("facebook", ""),
            "Instagram": socials.get("instagram", ""),
        })
    rows.sort(key=lambda r: r["Score"], reverse=True)
    return _df_to_csv(rows, INSTANTLY_COLUMNS)


def to_call_list_csv(leads: list[dict]) -> bytes:
    rows = []
    for lead in leads:
        if not _exportable(lead) or not lead.get("phone"):
            continue
        socials = lead.get("socials") or {}
        rows.append({
            "Business Name": lead.get("name") or "",
            "Phone": lead.get("phone") or "",
            "City": lead.get("city") or "",
            "Score": lead.get("lead_score", 0),
            "Has Website": "Yes" if lead.get("website") else "No",
            "Review Count": lead.get("reviews") or 0,
            "Facebook": socials.get("facebook", ""),
            "Notes": lead.get("score_reasons") or "",
        })
    rows.sort(key=lambda r: r["Score"], reverse=True)
    return _df_to_csv(rows, CALL_LIST_COLUMNS)


def to_full_csv(leads: list[dict]) -> bytes:
    rows = []
    for lead in leads:
        skip = ("email_details", "email_contacts", "socials")
        row = {k: v for k, v in lead.items() if not k.startswith("_") and k not in skip}
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
