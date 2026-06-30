"""Template-based cold-email icebreakers (the personalized first line).

Kept deliberately simple and free: picks a template based on what data the lead
actually has (no website / strong reviews / weak presence). Swap `make_icebreaker`
for an LLM call later if you want fully generated lines — the rest of the app
only depends on `lead["icebreaker"]` being a string.
"""

from __future__ import annotations


def add_icebreakers(leads: list[dict]) -> list[dict]:
    for lead in leads:
        lead["icebreaker"] = make_icebreaker(lead)
    return leads


def make_icebreaker(lead: dict) -> str:
    business = (lead.get("name") or "your business").strip()
    city = (lead.get("city") or "your area").strip()
    category = _category(lead)
    reviews = _to_int(lead.get("reviews"))
    rating = _to_float(lead.get("rating"))

    # No website → lead with the gap (these are the hottest leads).
    if not lead.get("website"):
        return (f"I was looking for {category} in {city} and came across {business}, "
                f"but couldn't find a website for you — are you taking on new customers right now?")

    # Strong, established business → open with a genuine compliment.
    if reviews >= 20 and rating is not None and rating >= 4.0:
        return (f"Came across {business} in {city} — {rating}★ from {reviews} reviews is genuinely "
                f"impressive. Had a quick idea I wanted to run by you.")

    # Weak online presence → low-review angle.
    if reviews < 10:
        return (f"Found {business} while looking at {category} in {city}. You're a bit harder to find "
                f"online than some competitors — wanted to reach out with a thought.")

    # Default.
    return (f"Came across {business} while researching {category} in {city} and wanted to get in touch.")


# ─────────────── helpers ───────────────

def _category(lead: dict) -> str:
    cat = lead.get("category")
    if isinstance(cat, list):
        cat = cat[0] if cat else None
    if not cat:
        return "local businesses"
    return str(cat).strip().lower()


def _to_int(v) -> int:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
