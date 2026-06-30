"""Duplicate detection.

A lead is a duplicate if ANY of its identity keys was already seen:
    1. place_id          (Google's own unique id)
    2. normalized phone
    3. any email
    4. normalized business-name + city   (light fuzzy: suffixes/punct stripped)

`identity_keys` is reused by `history.py` for cross-session dedup, so in-session
and across-session matching use exactly the same logic.
"""

from __future__ import annotations

import re

# Words dropped when normalizing a business name so "Joe's Plumbing LLC" and
# "Joe's Plumbing" collapse together.
_SUFFIXES = {"llc", "inc", "ltd", "co", "corp", "company", "the", "and", "&"}


def identity_keys(lead: dict) -> list[str]:
    """All string keys that identify this lead. Used for dedup + history."""
    keys: list[str] = []

    pid = lead.get("place_id")
    if pid:
        keys.append(f"pid:{pid}")

    phone = _norm_phone(lead.get("phone"))
    if phone:
        keys.append(f"phone:{phone}")

    for email in lead.get("emails", []) or []:
        email = (email or "").strip().lower()
        if email:
            keys.append(f"email:{email}")

    name = _norm_name(lead.get("name"))
    if name:
        city = _norm_text(lead.get("city"))
        keys.append(f"name:{name}|{city}")

    return keys


def deduplicate(leads: list[dict]) -> list[dict]:
    """Annotate each lead with `is_duplicate` / `duplicate_of`. Returns same list."""
    seen: dict[str, str] = {}
    for idx, lead in enumerate(leads):
        keys = identity_keys(lead)
        match = next((seen[k] for k in keys if k in seen), None)

        if match is not None:
            lead["is_duplicate"] = True
            lead["duplicate_of"] = match
        else:
            lead["is_duplicate"] = False
            lead.setdefault("duplicate_of", None)
            label = lead.get("name") or f"row {idx}"
            for k in keys:
                seen.setdefault(k, label)
    return leads


def unique_only(leads: list[dict]) -> list[dict]:
    return [l for l in leads if not l.get("is_duplicate")]


def duplicate_count(leads: list[dict]) -> int:
    return sum(1 for l in leads if l.get("is_duplicate"))


# ─────────────── normalizers ───────────────

def _norm_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 11 and digits.startswith("1"):  # strip US country code
        digits = digits[1:]
    return digits


def _norm_name(text: str | None) -> str:
    if not text:
        return ""
    # Drop apostrophes first so "Joe's" collapses to "joes" (not "joe s") and
    # matches a listing written as "Joes". Then turn other punctuation into
    # token breaks.
    lowered = str(text).lower().replace("'", "").replace("’", "")
    words = re.sub(r"[^a-z0-9 ]", " ", lowered).split()
    words = [w for w in words if w not in _SUFFIXES]
    return " ".join(words)


def _norm_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())
