"""Crash / disconnect resilience for an in-flight run.

If the network drops (browser↔app or app↔Outscraper) or the app restarts
mid-run, we must lose neither the Outscraper job handle nor the per-email /
per-domain work already done (and paid for). On resume we reload from disk and
skip anything already complete.

Files (under ./data):
    data/jobs/active.json        -> the single in-flight job + its scraped leads
    data/cache/validation.json   -> {email:  validation_result}   (reused across runs)
    data/cache/enrichment.json   -> {domain: contact_record}      (reused across runs)

Job stages:  submitted -> scraped -> enriched -> validated -> (cleared when done)

NOTE: like history.py, this is local-disk state. It survives a wifi drop or a
browser refresh, but NOT a host reboot on an ephemeral filesystem (Streamlit
Cloud) — see README → Deployment for durable storage.
"""

from __future__ import annotations

import json
import os

from .history import _atomic_write_text  # reuse the atomic (temp + os.replace) writer

BASE_DIR = "data"
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
ACTIVE_JOB = os.path.join(JOBS_DIR, "active.json")
VALIDATION_CACHE = os.path.join(CACHE_DIR, "validation.json")
ENRICHMENT_CACHE = os.path.join(CACHE_DIR, "enrichment.json")


def _dirs() -> None:
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


# ─────────────────────────── active job ───────────────────────────

def save_job(job: dict) -> None:
    _dirs()
    _atomic_write_text(ACTIVE_JOB, json.dumps(job))


def load_job() -> dict | None:
    return _load_json(ACTIVE_JOB, None)


def clear_job() -> None:
    try:
        os.remove(ACTIVE_JOB)
    except OSError:
        pass


def update_job(**fields) -> dict | None:
    """Merge fields into the active job and persist. No-op if no active job."""
    job = load_job()
    if job is None:
        return None
    job.update(fields)
    save_job(job)
    return job


# ─────────────────────────── per-key caches ───────────────────────────

class JsonCache:
    """A {key: value} store backed by a JSON file.

    Flushed atomically and throttled (every `flush_every` puts) so an
    interruption loses at most a handful of entries, never the whole file.
    """

    def __init__(self, path: str, flush_every: int = 10):
        self.path = path
        self.flush_every = max(1, flush_every)
        self._data: dict = _load_json(path, {})
        self._dirty = 0

    def __contains__(self, key) -> bool:
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def put(self, key, value) -> None:
        self._data[key] = value
        self._dirty += 1
        if self._dirty >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if self._dirty:
            _dirs()
            _atomic_write_text(self.path, json.dumps(self._data))
            self._dirty = 0

    def as_dict(self) -> dict:
        return self._data


def validation_cache(flush_every: int = 10) -> JsonCache:
    return JsonCache(VALIDATION_CACHE, flush_every)


def enrichment_cache(flush_every: int = 1) -> JsonCache:
    # Enrichment batches are expensive (credits), so flush after every batch.
    return JsonCache(ENRICHMENT_CACHE, flush_every)
