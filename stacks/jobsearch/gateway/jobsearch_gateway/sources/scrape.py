"""Tier 3 scrape fallback — planned v2, intentionally NOT implemented in v1.

Purpose (see README §Roadmap): fetch job-board pages that have no API and
block plain HTTP, using Scrapling's StealthyFetcher (realistic browser
fingerprints, Cloudflare solving), pinned to the current 0.4.x release line:

    # v2 requirements (scrape worker, not the gateway):
    scrapling[fetchers]~=0.4     # verify exact pin at implementation time

Why a stub in v1: StealthyFetcher pulls Patchright + a managed Chromium
(~500 MB+ image, several hundred MB RSS under load), which breaks the 2 GB
LXC budget and the under-an-hour deploy for a fallback that Tier 1+2
already cover (BA = Germany's largest board, SearXNG = open web).
The compose file carries a commented-out scrape-worker under profiles:
["scrape"] — v2 slots in here without touching main.py.
"""

from typing import Optional

from ..models import Job, SearchRequest
from ..throttle import SourceThrottle
from .base import SourceConnector


class GenericScrapeConnector(SourceConnector):
    name = "generic-scrape"
    tier = 3

    async def search(self, req: SearchRequest) -> list[Job]:
        raise NotImplementedError("planned v2 — see stacks/jobsearch/README.md §Roadmap")

    async def get_details(self, job: Job) -> Optional[dict]:
        return None

    async def health(self) -> bool:
        return False