"""Outscraper REST wrapper (NO SDK) — direct hits to the HTTP API.

Endpoints used:
    GET /maps/search-v3        -> business name, address, phone, website,
                                  rating, reviews, category, hours, coordinates
    GET /emails-and-contacts   -> emails + social links scraped from a website
    GET /email-validator       -> email deliverability (validator L4)
    GET /requests/{id}         -> poll an async job

Auth: header `X-API-KEY: <key>`.

Why REST instead of the SDK: stable endpoint names (no version drift), full
control over params/fields, raw JSON for debugging, and one less dependency.
Everything downstream still consumes the normalized schema from `normalize_lead`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests


# ───────────────────────── Config ──────────────────────────

@dataclass
class ScrapeConfig:
    niche: str
    locations: list[str]
    limit_per_query: int = 100
    language: str = "en"
    region: str | None = None
    drop_duplicates: bool = True
    extract_contacts: bool = False  # ask the API to enrich emails during search


class OutscraperError(RuntimeError):
    """Raised for any non-success response or network failure."""


# ───────────────────────── Client ──────────────────────────

class OutscraperClient:
    BASE_URL = "https://api.app.outscraper.com"

    def __init__(self, api_key: str | None = None, timeout: int = 600):
        api_key = api_key or os.getenv("OUTSCRAPER_API_KEY")
        if not api_key or api_key == "your_api_key_here":
            raise ValueError(
                "OUTSCRAPER_API_KEY is missing. Put it in .env or paste it in the sidebar."
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-KEY": api_key})

    # -- query building ------------------------------------------------

    @staticmethod
    def build_queries(niche: str, locations: list[str]) -> list[str]:
        niche = (niche or "").strip()
        if not niche:
            raise ValueError("Niche/keyword cannot be empty.")
        queries = [f"{niche} in {loc.strip()}" for loc in locations if loc and loc.strip()]
        if not queries:
            raise ValueError("At least one location is required.")
        return queries

    # -- maps search ---------------------------------------------------

    def search_sync(self, cfg: ScrapeConfig) -> list[dict]:
        """Blocking search. Submits async=false; transparently polls if the API
        decides the job is too big to answer in one shot."""
        data = self._get_with_polling("/maps/search-v3", self._maps_params(cfg))
        return [normalize_lead(r) for r in _flatten(data)]

    def search_async(self, cfg: ScrapeConfig) -> str:
        """Fire-and-forget: returns a request_id to poll later via poll_async."""
        params = self._maps_params(cfg) + [("async", "true")]
        body = self._get("/maps/search-v3", params)
        request_id = body.get("id")
        if not request_id:
            raise OutscraperError(f"No request id in async response: {body}")
        return request_id

    def poll_async(self, request_id: str) -> dict:
        """Return {'status': 'Pending'|'Success'|'Error', 'leads': [...], 'raw': {...}}"""
        body = self._get(f"/requests/{request_id}", [])
        status = body.get("status", "Pending")
        leads: list[dict] = []
        if status == "Success":
            leads = [normalize_lead(r) for r in _flatten(body.get("data", []))]
        return {"status": status, "leads": leads, "raw": body}

    def _maps_params(self, cfg: ScrapeConfig) -> list[tuple]:
        params: list[tuple] = [("query", q) for q in self.build_queries(cfg.niche, cfg.locations)]
        params += [
            ("limit", cfg.limit_per_query),
            ("language", cfg.language),
            ("dropDuplicates", _bool(cfg.drop_duplicates)),
        ]
        if cfg.region:
            params.append(("region", cfg.region))
        if cfg.extract_contacts:
            params.append(("enrichment", "domains_service"))
        return params

    # -- contacts & emails (used by enrichment.py) ---------------------

    def emails_and_contacts(self, domains: list[str]) -> list[dict]:
        params = [("query", d) for d in domains]
        data = self._get_with_polling("/emails-and-contacts", params)
        return _flatten(data)

    # -- email validator (used by validator.py L4) ---------------------

    def validate_email_api(self, email: str) -> dict | None:
        for endpoint in ("/email-validator", "/emails-validator"):
            try:
                data = self._get_with_polling(endpoint, [("query", email)])
                rows = _flatten(data)
                if rows:
                    return rows[0]
            except OutscraperError:
                continue
        return None

    # -- low-level HTTP ------------------------------------------------

    def _get(self, endpoint: str, params: list[tuple]) -> dict:
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise OutscraperError(f"Network error calling {endpoint}: {e}") from e

        if resp.status_code == 401:
            raise OutscraperError("Unauthorized (401) — check your API key.")
        if resp.status_code == 402:
            raise OutscraperError("Payment required (402) — out of Outscraper credits.")
        if resp.status_code not in (200, 202):
            raise OutscraperError(f"{endpoint} returned {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as e:
            raise OutscraperError(f"{endpoint} returned non-JSON: {resp.text[:300]}") from e

    def _get_with_polling(self, endpoint: str, params: list[tuple],
                          poll_interval: int = 5, max_wait: int | None = 600) -> list:
        """Submit synchronously; if the API parks it as a job, poll until done."""
        body = self._get(endpoint, params + [("async", "false")])
        if body.get("status") == "Success":
            return body.get("data", [])

        request_id = body.get("id")
        if not request_id:
            raise OutscraperError(f"Unexpected response from {endpoint}: {body}")

        deadline = time.time() + max_wait if max_wait else None
        while True:
            res = self.poll_async(request_id)
            if res["status"] == "Success":
                return res["raw"].get("data", [])
            if res["status"] == "Error":
                raise OutscraperError(f"Job {request_id} failed: {res['raw']}")
            if deadline and time.time() > deadline:
                raise OutscraperError(f"Timed out after {max_wait}s waiting for {request_id}.")
            time.sleep(poll_interval)


# ───────────────────────── Helpers ─────────────────────────

def _bool(value: bool) -> str:
    return "true" if value else "false"


def _flatten(results: Any) -> list[dict]:
    """API returns data as a list-of-lists (one inner list per query)."""
    rows: list[dict] = []
    if not results:
        return rows
    for group in results:
        if isinstance(group, list):
            rows.extend(g for g in group if isinstance(g, dict))
        elif isinstance(group, dict):
            rows.append(group)
    return rows


def _format_hours(hours) -> str | None:
    """working_hours arrives as {'Monday': '9AM-5PM', ...}; flatten to a string."""
    if not hours:
        return None
    if isinstance(hours, str):
        return hours
    if isinstance(hours, dict):
        return "; ".join(f"{day}: {val}" for day, val in hours.items())
    return str(hours)


def normalize_lead(raw: dict) -> dict:
    """Map Outscraper's raw record into the canonical schema used everywhere."""
    contacts: list[dict] = []
    emails: list[str] = []
    for i in (1, 2, 3):
        value = raw.get(f"email_{i}")
        if value:
            emails.append(value)
            contacts.append({
                "email": value,
                "full_name": raw.get(f"email_{i}_full_name"),
                "title": raw.get(f"email_{i}_title"),
            })

    return {
        "name": raw.get("name"),
        "website": raw.get("site") or raw.get("website"),
        "phone": raw.get("phone") or raw.get("phone_1"),
        "full_address": raw.get("full_address"),
        "city": raw.get("city"),
        "state": raw.get("state") or raw.get("us_state"),
        "postal_code": raw.get("postal_code"),
        "country": raw.get("country"),
        "rating": raw.get("rating"),
        "reviews": raw.get("reviews"),
        "photos_count": raw.get("photos_count"),
        "category": raw.get("type") or raw.get("category") or raw.get("subtypes"),
        "working_hours": _format_hours(raw.get("working_hours")),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "verified": raw.get("verified"),
        "place_id": raw.get("place_id") or raw.get("google_id"),
        "google_maps_url": raw.get("location_link"),
        "query": raw.get("query"),
        # populated by later stages
        "emails": emails,
        "email_contacts": contacts,
        "socials": {},
        "_raw": raw,
    }
