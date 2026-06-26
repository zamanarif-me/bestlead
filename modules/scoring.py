"""Lead scoring for outreach prioritization.

The scoring is tuned for selling services (web design / SEO / marketing):
a business with NO website and FEW reviews is a *hot* opportunity, not a
low-quality record. Tweak the weights in `DEFAULT_WEIGHTS` to fit your offer.
"""

from __future__ import annotations

DEFAULT_WEIGHTS = {
    "no_website": 35,        # biggest buying signal for web/marketing services
    "no_reviews": 20,
    "few_reviews": 12,       # < 10 reviews
    "low_rating": 10,        # rating < 4.0
    "unverified": 8,         # unclaimed Google listing
    "has_phone": 10,         # reachable by call
    "has_email": 8,          # reachable by email
    "direct_email": 12,      # decision-maker reachable directly
}

HOT, WARM = 70, 40


def score_lead(lead: dict, weights: dict | None = None) -> dict:
    w = weights or DEFAULT_WEIGHTS
    score = 0
    reasons: list[str] = []

    if not lead.get("website"):
        score += w["no_website"]
        reasons.append("No website")

    reviews = _to_int(lead.get("reviews"))
    if reviews == 0:
        score += w["no_reviews"]
        reasons.append("No reviews")
    elif reviews < 10:
        score += w["few_reviews"]
        reasons.append("Few reviews (<10)")

    rating = _to_float(lead.get("rating"))
    if rating is not None and rating < 4.0:
        score += w["low_rating"]
        reasons.append(f"Low rating ({rating})")

    if lead.get("verified") is False:
        score += w["unverified"]
        reasons.append("Unverified listing")

    if lead.get("phone"):
        score += w["has_phone"]
        reasons.append("Has phone")

    if lead.get("email_best") and lead.get("email_status") != "Dead":
        score += w["has_email"]
        reasons.append("Has email")
        if lead.get("email_best_type") == "Direct":
            score += w["direct_email"]
            reasons.append("Direct email")

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
