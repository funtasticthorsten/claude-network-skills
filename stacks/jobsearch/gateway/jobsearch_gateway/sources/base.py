"""Source connector interface. Add a source = one new module here + one registry
line in sources/__init__.py. main.py never changes."""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Job, SearchRequest
from ..throttle import SourceThrottle


class SourceConnector(ABC):
    name: str = "base"   # registry key, used in `sources` request field
    tier: int = 1        # 1 = official API, 2 = meta-search, 3 = scrape fallback

    def __init__(self, throttle: SourceThrottle):
        self.throttle = throttle

    @abstractmethod
    async def search(self, req: SearchRequest) -> list[Job]:
        """Fetch and normalize results for one search request."""

    async def get_details(self, job: Job) -> Optional[dict]:
        """Return source-specific details dict, or None if unsupported.
        The gateway caches whatever comes back for DETAILS_TTL."""
        return None

    @abstractmethod
    async def health(self) -> bool:
        """Cheap liveness probe of the upstream dependency."""