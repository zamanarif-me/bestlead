"""Duplicate detection.

Across multiple locations/queries the same business can appear more than once.
We collapse on the strongest available identity key, in order:

    1. place_id          (Google's own unique id — most reliable)
    2. normalized phone
    3. normalized name + address

The first occurrence is kept as the "primary"; later ones are flagged
`is_duplicate=True` so the UI/exporter can drop or review them.
"""

from __future__ import annotations

import re


def deduplicate(leads: list[dict]) -> list[dict]:
    """Annotate each lead with `is_duplicate` / `duplicate_of`. Returns same list."""
    seen: dict[tuple, str] = {}
    for idx, lead in enumerate(leads):
        key = _dedup_key(lead, idx)
        if key in seen:
            lead["is_duplicate"] = True
            lead["duplicate_of"] = seen[key]
        else:
            lead["is_duplicate"] = False
            lead["duplicate_of"] = None
            seen[key] = lead.get("name") or f"row {idx}"
    return leads


def unique_only(leads: list[dict]) -> list[dict]:
    return [l for l in leads if not l.get("is_duplicate")]


def duplicate_count(leads: list[dict]) -> int:
    return sum(1 for l in leads if l.get("is_duplicate"))


# ─────────────── internals ───────────────

def _dedup_key(lead: dict, idx: int) -> tuple:
    place_id = lead.get("place_id")
    if place_id:
        return ("pid", str(place_id))

    phone = _norm_phone(lead.get("phone"))
    if phone:
        return ("phone", phone)

    name = _norm_text(lead.get("name"))
    addr = _norm_text(lead.get("full_address"))
    if name:
        return ("name_addr", name, addr)

    # Nothing to key on — treat as unique.
    return ("row", idx)


def _norm_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    # Drop a leading country '1' for US-style numbers so +1 and bare match.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _norm_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())
