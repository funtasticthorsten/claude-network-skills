"""SearXNG meta-search connector — Tier 2.

Queries the self-hosted SearXNG instance's JSON API for broad/open web
coverage beyond the BA database. SearXNG rotates upstream engines, which
spreads requests across providers — the gateway adds a polite min-interval
on top (upstream engines are shared with any interactive use).

Results are LEADS, not structured postings: title, url, snippet. No details
fetch — the agent follows `job.url` itself with its own tools.
"""

import hashlib
from typing import Optional

import httpx

from ..config import settings
from ..models import Job, SearchRequest
from ..throttle import SourceThrottle
from .base import SourceConnector

REQUEST_TIMEOUT_S = 15.0


class SearxngConnector(SourceConnector):
    name = "searxng"
    tier = 2

    def __init__(self, throttle: SourceThrottle):
        super().__init__(throttle)
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            headers={"Accept": "application/json", "User-Agent": settings.user_agent},
        )

    def _build_query(self, req: SearchRequest) -> str:
        terms = [req.query]
        if req.location:
            terms.append(f"{req.location}")
        site_filters = settings.site_filter_list
        if site_filters:
            terms.append("(" + " OR ".join(f"site:{d}" for d in site_filters) + ")")
        return " ".join(terms)

    async def search(self, req: SearchRequest) -> list[Job]:
        params = {
            "q": self._build_query(req),
            "format": "json",
            "language": "de-DE",
            "pageno": req.page,
        }
        if req.age_days is not None:
            params["time_range"] = "month" if req.age_days <= 30 else "year"

        async with self.throttle:
            resp = await self._client.get(f"{settings.searxng_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        jobs = []
        for r in data.get("results", [])[: req.size]:
            url = r.get("url", "")
            if not url:
                continue
            jobs.append(
                Job(
                    id=f"{self.name}:{hashlib.sha1(url.encode()).hexdigest()[:12]}",
                    source=self.name,
                    refnr=url,
                    title=r.get("title", ""),
                    company=None,
                    published=r.get("publishedDate"),
                    url=url,
                    description_snippet=r.get("content")[:300] if r.get("content") else None,
                )
            )
        return jobs

    async def get_details(self, job: Job) -> Optional[dict]:
        # SearXNG rows are leads — no structured details available.
        return None

    async def health(self) -> bool:
        try:
            async with self.throttle:
                resp = await self._client.get(
                    f"{settings.searxng_url}/search",
                    params={"q": "test", "format": "json"},
                )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False