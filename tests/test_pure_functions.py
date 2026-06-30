"""Unit tests for the pure, network-free functions in the pipeline.

These cover the parts most likely to break silently on an Outscraper schema
quirk or a refactor: normalization, identity/dedup, email classification,
scoring, and CSV export filtering. Nothing here touches the network.

Run from the project root:
    pip install -r requirements-dev.txt
    pytest
"""

from __future__ import annotations

import os

import pytest

from modules import checkpoint, dedup, enrichment, exporter, history, icebreaker, scoring
from modules.scraper import normalize_lead
from modules.validator import EmailValidator, _cacheable, _pick_best, classify_type


# ───────────────── scraper.normalize_lead ─────────────────

def test_normalize_lead_collects_flat_emails_and_fallbacks():
    raw = {
        "name": "Acme Plumbing", "site": "acme.com",
        "email_1": "a@acme.com", "email_1_full_name": "Ann Acme",
        "email_2": "b@acme.com",
        "phone_1": "+1 555-0100", "google_id": "g1",
    }
    lead = normalize_lead(raw)
    assert lead["name"] == "Acme Plumbing"
    assert lead["website"] == "acme.com"
    assert lead["emails"] == ["a@acme.com", "b@acme.com"]
    assert lead["phone"] == "+1 555-0100"          # falls back to phone_1
    assert lead["place_id"] == "g1"                 # falls back to google_id
    assert lead["email_contacts"][0]["full_name"] == "Ann Acme"


def test_normalize_lead_handles_empty_record():
    lead = normalize_lead({})
    assert lead["emails"] == []
    assert lead["name"] is None
    assert lead["socials"] == {}


# ───────────────── enrichment._domain_of ─────────────────

def test_domain_of_strips_scheme_and_www_and_lowercases():
    assert enrichment._domain_of("https://www.Acme.com/contact") == "acme.com"
    assert enrichment._domain_of("acme.com") == "acme.com"
    assert enrichment._domain_of("http://WWW.Foo.CO.UK") == "foo.co.uk"
    assert enrichment._domain_of(None) is None
    assert enrichment._domain_of("") is None


# ───────────────── dedup ─────────────────

def test_norm_phone_strips_us_country_code_and_punct():
    assert dedup._norm_phone("+1 (555) 123-4567") == "5551234567"
    assert dedup._norm_phone("555.123.4567") == "5551234567"
    assert dedup._norm_phone(None) == ""


def test_norm_name_drops_suffixes_and_punct():
    assert dedup._norm_name("Joe's Plumbing LLC") == dedup._norm_name("Joes Plumbing")
    assert dedup._norm_name("The Roof Co.") == "roof"


def test_deduplicate_matches_on_place_id():
    a = {"place_id": "p1", "name": "Joe Plumbing", "city": "Austin", "phone": "555-1", "emails": []}
    b = {"place_id": "p1", "name": "Totally Different", "city": "Dallas", "phone": "999", "emails": []}
    leads = [a, b]
    dedup.deduplicate(leads)
    assert a["is_duplicate"] is False
    assert b["is_duplicate"] is True            # same place_id wins
    assert dedup.duplicate_count(leads) == 1


def test_deduplicate_matches_on_name_plus_city():
    a = {"name": "Joe's Plumbing LLC", "city": "Austin", "emails": []}
    b = {"name": "Joes Plumbing", "city": "Austin", "emails": []}
    leads = [a, b]
    dedup.deduplicate(leads)
    assert b["is_duplicate"] is True


# ───────────────── validator (network-free) ─────────────────

def test_classify_type():
    assert classify_type("john@acme.com") == "Direct"
    assert classify_type("info@acme.com") == "Generic"
    assert classify_type("jane@gmail.com") == "Personal"


def test_syntax_only_validation():
    v = EmailValidator(levels=("syntax",))             # no network levels
    good = v.validate_email("good@acme.com")
    assert good["syntax"] is True
    assert good["status"] == "Risky"                   # passes syntax, no positive signal
    assert v.validate_email("not-an-email")["status"] == "Dead"


def test_pick_best_prefers_valid_direct_over_risky_generic():
    details = [
        {"email": "info@a.com", "status": "Risky", "type": "Generic"},
        {"email": "jo@a.com", "status": "Valid", "type": "Direct"},
        {"email": "x@a.com", "status": "Dead", "type": "Dead"},
    ]
    assert _pick_best(details)["email"] == "jo@a.com"
    assert _pick_best([]) is None


# ───────────────── scoring ─────────────────

def test_score_hot_no_website():
    lead = {"website": None, "reviews": 3, "rating": 3.0, "photos_count": 0,
            "email_best": None, "email_status": "No email"}
    scoring.score_lead(lead)
    # 40 (no site) + 15 (few reviews) + 10 (low rating) + 10 (no photos) - 10 (no email) = 65
    assert lead["lead_score"] == 65
    assert lead["lead_label"] == "Hot"


def test_score_cold_and_clamped_at_zero():
    lead = {"website": "x.com", "reviews": 100, "rating": 4.9, "photos_count": 5,
            "email_best": "a@x.com", "email_status": "Valid"}
    scoring.score_lead(lead)
    assert lead["lead_score"] == 0                      # only the -10 email penalty would apply, clamped
    assert lead["lead_label"] == "Cold"


def test_score_leads_sorts_desc():
    leads = [
        {"website": "x.com", "reviews": 50, "rating": 5, "photos_count": 1,
         "email_best": "a@x.com", "email_status": "Valid"},
        {"website": None, "reviews": 1, "rating": 2.0, "photos_count": 0,
         "email_best": None, "email_status": "No email"},
    ]
    scoring.score_leads(leads)
    assert leads[0]["lead_score"] >= leads[1]["lead_score"]


# ───────────────── icebreaker ─────────────────

def test_icebreaker_no_website_angle():
    line = icebreaker.make_icebreaker({"name": "Joe", "city": "Austin", "website": None})
    assert "Joe" in line and "Austin" in line


# ───────────────── exporter ─────────────────

def test_split_name():
    assert exporter._split_name("John Doe") == ("John", "Doe")
    assert exporter._split_name("Maria Van Der Berg") == ("Maria", "Van Der Berg")
    assert exporter._split_name("Cher") == ("Cher", "")
    assert exporter._split_name("") == ("", "")


def test_instantly_csv_skips_dead_dupes_and_seen():
    leads = [
        {"email_best": "a@x.com", "email_status": "Valid", "email_best_type": "Direct",
         "name": "A", "lead_score": 50, "is_duplicate": False, "seen_before": False,
         "emails": ["a@x.com"], "email_contacts": []},
        {"email_best": "b@x.com", "email_status": "Dead", "name": "B",
         "is_duplicate": False, "seen_before": False, "emails": ["b@x.com"]},
        {"email_best": "c@x.com", "email_status": "Valid", "name": "C",
         "is_duplicate": True, "seen_before": False, "emails": ["c@x.com"]},
        {"email_best": "d@x.com", "email_status": "Valid", "name": "D",
         "is_duplicate": False, "seen_before": True, "emails": ["d@x.com"]},
    ]
    csv = exporter.to_instantly_csv(leads).decode()
    assert "a@x.com" in csv          # valid, unique, unseen -> included
    assert "b@x.com" not in csv      # dead
    assert "c@x.com" not in csv      # in-batch duplicate
    assert "d@x.com" not in csv      # seen in a past session


# ───────────────── history (atomic write) ─────────────────

def test_atomic_write_replaces_file(tmp_path):
    target = tmp_path / "idx.json"
    history._atomic_write_text(str(target), '{"k": "v"}')
    assert target.read_text(encoding="utf-8") == '{"k": "v"}'
    # No stray temp parts left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["idx.json"]


# ───────────────── checkpoint / resume ─────────────────

@pytest.fixture
def cp_tmp(tmp_path, monkeypatch):
    """Redirect checkpoint storage into a tmp dir so tests don't touch ./data."""
    monkeypatch.setattr(checkpoint, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(checkpoint, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(checkpoint, "ACTIVE_JOB", str(tmp_path / "jobs" / "active.json"))
    return tmp_path


def test_job_save_load_update_clear(cp_tmp):
    assert checkpoint.load_job() is None
    checkpoint.save_job({"job_id": "x", "stage": "submitted"})
    assert checkpoint.load_job()["stage"] == "submitted"
    checkpoint.update_job(stage="scraped", leads=[{"a": 1}])
    job = checkpoint.load_job()
    assert job["stage"] == "scraped" and job["leads"] == [{"a": 1}]
    assert job["job_id"] == "x"                 # untouched fields survive a merge
    checkpoint.clear_job()
    assert checkpoint.load_job() is None


def test_update_job_noop_without_active_job(cp_tmp):
    assert checkpoint.update_job(stage="scraped") is None


def test_jsoncache_throttles_then_persists(cp_tmp):
    path = os.path.join(checkpoint.CACHE_DIR, "c.json")
    c = checkpoint.JsonCache(path, flush_every=2)
    c.put("a", 1)
    assert not os.path.exists(path)             # below threshold, not yet written
    c.put("b", 2)
    assert os.path.exists(path)                  # threshold hit -> flushed
    reloaded = checkpoint.JsonCache(path)
    assert "a" in reloaded and reloaded.get("b") == 2


def test_jsoncache_manual_flush(cp_tmp):
    path = os.path.join(checkpoint.CACHE_DIR, "c.json")
    c = checkpoint.JsonCache(path, flush_every=100)
    c.put("a", 1)
    c.flush()
    assert checkpoint.JsonCache(path).get("a") == 1


# ───────────────── resume caches skip already-done work ─────────────────

class _FakeCache:
    def __init__(self, seed=None):
        self.d = dict(seed or {})
        self.puts = []

    def __contains__(self, k):
        return k in self.d

    def get(self, k, default=None):
        return self.d.get(k, default)

    def put(self, k, v):
        self.d[k] = v
        self.puts.append(k)

    def flush(self):
        pass


def test_cacheable_filter():
    assert _cacheable({"status": "Valid"})
    assert _cacheable({"status": "Risky"})
    assert _cacheable({"status": "Dead", "syntax": False})        # deterministic syntax fail
    assert not _cacheable({"status": "Dead", "syntax": True})     # could be a net blip -> recheck
    assert not _cacheable({"status": "Unknown"})


def test_validator_skips_cached_emails(monkeypatch):
    v = EmailValidator(levels=("syntax",))
    calls = []
    real = v.validate_email
    monkeypatch.setattr(v, "validate_email", lambda e: calls.append(e) or real(e))

    cache = _FakeCache(seed={"cached@a.com": {"email": "cached@a.com", "status": "Valid", "type": "Direct"}})
    leads = [{"emails": ["cached@a.com", "new@a.com"]}]
    v.validate_leads(leads, cache=cache)

    assert calls == ["new@a.com"]                 # cached email was NOT re-validated (no re-charge)
    assert "new@a.com" in cache.d                  # newly validated email is now cached
    assert leads[0]["email_best"] == "cached@a.com"  # Valid beats the new Risky one


def test_enrichment_skips_cached_domains():
    cache = _FakeCache(seed={"a.com": {"emails": [{"value": "x@a.com"}], "facebook": "fb/a"}})

    class FakeClient:
        def __init__(self):
            self.queried = []

        def emails_and_contacts(self, domains):
            self.queried += domains
            return [[{"query": d, "email_1": f"new@{d}"} for d in domains]]

    client = FakeClient()
    leads = [
        {"website": "http://a.com", "emails": [], "socials": {}},
        {"website": "http://b.com", "emails": [], "socials": {}},
    ]
    enrichment.enrich_leads(client, leads, mode="full", cache=cache)

    assert client.queried == ["b.com"]             # a.com served from cache, only b.com crawled
    assert "x@a.com" in leads[0]["emails"]          # cached email merged in
    assert leads[0]["socials"].get("facebook") == "fb/a"
    assert "new@b.com" in leads[1]["emails"]        # freshly crawled email merged in
