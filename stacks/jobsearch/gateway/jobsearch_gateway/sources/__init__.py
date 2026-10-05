"""Connector registry. Add a source = one new module + one entry here."""

from ..config import settings
from ..throttle import SourceThrottle
from .base import SourceConnector
from .bundesagentur import BundesagenturConnector
from .eures import EuresConnector
from .scrape import GenericScrapeConnector
from .searxng import SearxngConnector

# Polite per-source intervals (seconds) — be a good upstream citizen.
MIN_INTERVALS = {
    "bundesagentur": 1.0,  # federal government service
    "searxng": 2.0,        # shares upstream engines with interactive use
    "eures": 1.0,
    "generic-scrape": 5.0,
}

# Per-source request timeouts for the fan-out (seconds).
SOURCE_TIMEOUTS = {
    "bundesagentur": 10.0,
    "searxng": 15.0,
    "eures": 10.0,
    "generic-scrape": 15.0,
}


def build_connectors() -> dict[str, SourceConnector]:
    """Instantiate every enabled connector from JOBSEARCH_SOURCES."""
    factories = {
        "bundesagentur": BundesagenturConnector,
        "searxng": SearxngConnector,
        "eures": EuresConnector,
        "generic-scrape": GenericScrapeConnector,
    }
    connectors: dict[str, SourceConnector] = {}
    for name in settings.enabled_sources:
        factory = factories.get(name)
        if factory is None:
            continue  # unknown name in .env — skip rather than crash the stack
        if name == "eures" and not settings.eures_api_key:
            continue  # env-gated: stays disabled without a registered key
        connectors[name] = factory(SourceThrottle(MIN_INTERVALS.get(name, 2.0)))
    return connectors


REGISTRY = build_connectors()