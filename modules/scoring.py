"""Lead scoring for outreach prioritization (Digital Zeon weights).

Tuned for selling services to home-service businesses: a business with NO
website and few reviews is a *hot* opportunity, not a low-quality record.

Point system (only signals we can actually compute from Outscraper data):
    No website ............... +40
    Reviews < 10 ............. +15
    Rating < 3.5 ............. +10
    No GBP photos ............ +10
    No usable email .......... -10   (penalty — harder to reach by email)

Labels:  🔥 Hot 60+   ✅ Warm 35-59   💤 Cold <35

NOTE: "Website but mobile-unfriendly +25" from the spec is intentionally left
out — that signal needs a separate API (e.g. Google PageSpeed) and isn't in the
Maps data. Hook is left below if you want to add it later.
"""

from __future__ import annotations

DEFAULT_WEIGHTS = {
    "no_website": 40,
    "few_reviews": 15,    # < 10 reviews
    "low_rating": 10,     # < 3.5
    "no_photos": 10,      # no Google Business Profile photos
    "no_email": -10,      # penalty when no usable email is found
    # "mobile_unfriendly": 25,  # needs PageSpeed API — not wired yet
}

HOT, WARM = 60, 35


def score_lead(lead: dict, weights: dict | None = None) -> dict:
    w = weights or DEFAULT_WEIGHTS
    score = 0
    reasons: list[str] = []

    if not lead.get("website"):
        score += w["no_website"]
        reasons.append("No website")

    reviews = lead.get("reviews")
    if reviews is not None and _to_int(reviews) < 10:
        score += w["few_reviews"]
        reasons.append("Few reviews (<10)")

    rating = _to_float(lead.get("rating"))
    if rating is not None and rating < 3.5:
        score += w["low_rating"]
        reasons.append(f"Low rating ({rating})")

    photos = lead.get("photos_count")
    if photos is not None and _to_int(photos) == 0:
        score += w["no_photos"]
        reasons.append("No photos")

    has_email = bool(lead.get("email_best")) and lead.get("email_status") != "Dead"
    if not has_email:
        score += w["no_email"]
        reasons.append("No usable email (-)")

    score = max(0, min(score, 100))
    lead["lead_score"] = score
    lead["lead_label"] = "Hot" if score >= HOT else "Warm" if score >= WARM else "Cold"
    lead["score_reasons"] = "; ".join(reasons)
    return lead


def score_leads(leads: list[dict], weights: dict | None = None) -> list[dict]:
    for lead in leads:
        score_lead(lead, weights)
    leads.sort(key=lambda x: x.get("lead_score", 0), reverse=True)
    return leads


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
