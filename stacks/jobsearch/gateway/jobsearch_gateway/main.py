"""jobsearch-gateway — FastAPI app: bearer auth, source fan-out, persistence.

Agents (Hermes, Claude Code via the MCP wrapper, plain HTTP clients) talk to
/v1/search, /v1/jobs, /v1/jobs/{id}, /v1/sources. A failing source degrades
gracefully: the search still returns 200 with the other sources' results.
"""

import asyncio
import hashlib
import json
import os
import secrets as pysecrets
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .config import settings
from .db import Database
from .dedup import dedup, with_hashes
from .models import JobDetails, Job, SearchRequest, SearchResponse, SourceStatus
from .sources import REGISTRY, SOURCE_TIMEOUTS
from .sources.base import SourceConnector

app = FastAPI(
    title="jobsearch-gateway",
    version=__version__,
    docs_url=None,  # no public Swagger on an internal service
    redoc_url=None,
)

_security = HTTPBearer(auto_error=False)
_db: Optional[Database] = None
_db_lock = asyncio.Lock()

# In-process search cache: {(source, param-sha): (monotonic ts, [Job])}
SEARCH_CACHE_TTL_S = 3600
_cache: dict = {}


def get_db() -> Database:
    global _db
    if _db is None:
        os.makedirs(os.path.dirname(settings.database_path) or ".", exist_ok=True)
        _db = Database(settings.database_path)
    return _db


async def require_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> None:
    if not settings.api_token:
        raise HTTPException(status_code=503, detail="JOBSEARCH_API_TOKEN not configured")
    if creds is None or not pysecrets.compare_digest(
        creds.credentials.encode(), settings.api_token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


# -- health -------------------------------------------------------------------

@app.get("/health")
async def health():
    # Deliberately does NOT probe upstreams: the compose healthcheck hits this
    # every 30 s and a real probe would poll the BA API ~3000x/day. Use
    # /v1/sources for authenticated, real upstream health checks.
    sources = {
        name: {"tier": connector.tier, "enabled": True}
        for name, connector in REGISTRY.items()
    }
    return {"status": "ok", "version": __version__, "sources": sources}


# -- search ---------------------------------------------------------------------

async def _search_one(
    connector: SourceConnector, req: SearchRequest, cache_key: str
) -> tuple[list[Job], SourceStatus]:
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < SEARCH_CACHE_TTL_S:
        return cached[1], SourceStatus(
            status="ok", count=len(cached[1]), cached=True
        )
    start = time.monotonic()
    try:
        jobs = await asyncio.wait_for(
            connector.search(req), SOURCE_TIMEOUTS.get(connector.name, 10.0)
        )
        _cache[cache_key] = (time.monotonic(), jobs)
        if len(_cache) > 256:  # crude cap; homelab cache, restart clears it
            _cache.clear()
        return jobs, SourceStatus(
            status="ok",
            count=len(jobs),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except NotImplementedError as exc:
        return [], SourceStatus(status="error", error=str(exc))
    except Exception as exc:
        return [], SourceStatus(
            status="error",
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


@app.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest, _: None = Depends(require_token)
) -> SearchResponse:
    requested = req.sources or list(REGISTRY.keys())
    unknown = [s for s in requested if s not in REGISTRY]
    results = await asyncio.gather(
        *(
            _search_one(
                REGISTRY[name],
                req,
                hashlib.sha256(
                    json.dumps(
                        {
                            "source": name,
                            "query": req.query,
                            "location": req.location,
                            "radius": req.radius_km,
                            "page": req.page,
                            "size": req.size,
                            "age": req.age_days,
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            )
            for name in requested
            if name in REGISTRY
        )
    )
    statuses = {
        name: result[1]  # gather() yields (jobs, SourceStatus) per source
        for name, result in zip([s for s in requested if s in REGISTRY], results)
    }
    for name in unknown:
        statuses[name] = SourceStatus(
            status="skipped", error=f"source not enabled (enabled: {list(REGISTRY)})"
        )

    all_jobs = [job for jobs, _ in results for job in jobs]
    all_jobs = with_hashes(all_jobs)
    unique, removed = dedup(all_jobs)

    if req.persist:
        async with _db_lock:
            await asyncio.to_thread(get_db().upsert_jobs, unique)
        for job in unique:
            job.details_url = f"/v1/jobs/{job.id}"

    return SearchResponse(
        jobs=unique,
        sources=statuses,
        total=len(unique),
        duplicates_removed=removed,
    )


# -- persisted jobs ----------------------------------------------------------------

@app.get("/v1/jobs", response_model=list[Job])
async def list_jobs(
    q: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[str] = None,
    remote: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    _: None = Depends(require_token),
):
    limit = max(1, min(limit, 200))
    async with _db_lock:
        return await asyncio.to_thread(
            get_db().list_jobs,
            q=q,
            source=source,
            since=since,
            remote=remote,
            limit=limit,
            offset=offset,
        )


# -- details ---------------------------------------------------------------------

@app.get("/v1/jobs/{job_id}", response_model=JobDetails)
async def get_job_details(job_id: str, _: None = Depends(require_token)):
    db = get_db()
    async with _db_lock:
        cached = await asyncio.to_thread(db.get_details, job_id)
    if cached is not None:
        return JobDetails(**cached, cached=True)

    job = await asyncio.to_thread(db.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job id: {job_id}")

    connector = REGISTRY.get(job.source)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"source not enabled: {job.source}")

    details = await connector.get_details(job)
    if details is None:
        # searxng leads and sources without a details endpoint: hand back
        # what we know and let the agent follow job.url itself
        details = {
            "id": job.id,
            "source": job.source,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "description": job.description_snippet,
            "raw": {},
        }
    else:
        details["id"] = job.id
        details.setdefault("source", job.source)
        details.setdefault("url", job.url)
    async with _db_lock:
        await asyncio.to_thread(db.save_details, job_id, details)
    return JobDetails(**details, cached=False)


# -- sources ---------------------------------------------------------------------

@app.get("/v1/sources")
async def list_sources(_: None = Depends(require_token)):
    return [
        {
            "name": name,
            "tier": connector.tier,
            "enabled": True,
            "healthy": await connector.health(),
        }
        for name, connector in REGISTRY.items()
    ]