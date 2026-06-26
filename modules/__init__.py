"""Lead Scraper modules package.

Pipeline:
    scraper  -> enrichment -> validator -> scoring -> dedup -> exporter

Every stage operates on a list of *normalized lead dicts* (see
`scraper.normalize_lead`) so the modules stay decoupled and individually
testable.
"""

from . import scraper, enrichment, validator, scoring, dedup, exporter  # noqa: F401

__all__ = ["scraper", "enrichment", "validator", "scoring", "dedup", "exporter"]
