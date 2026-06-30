"""Lead Scraper modules package.

Pipeline:
    scraper -> enrichment -> validator -> scoring -> icebreaker -> dedup
            -> history (cross-session) -> exporter

Every stage operates on a list of *normalized lead dicts* (see
`scraper.normalize_lead`) so the modules stay decoupled and individually
testable.
"""

from . import (  # noqa: F401
    scraper, enrichment, validator, scoring, icebreaker, dedup, history, exporter,
)

__all__ = [
    "scraper", "enrichment", "validator", "scoring",
    "icebreaker", "dedup", "history", "exporter",
]
