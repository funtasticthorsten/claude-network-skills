"""MCP wrapper for the jobsearch gateway (stdio transport).

Runs on the AGENT host (spawned by the agent runtime — Claude Code, Hermes
Agent, any MCP-capable client), NOT inside the LXC. It only needs network
reachability to the gateway's REST API.

Config via environment:
  JOBSEARCH_GATEWAY_URL   e.g. http://192.168.30.10:8080
  JOBSEARCH_API_TOKEN      the same bearer token as in the stack's .env

HTTP-capable agents can skip MCP entirely and call the REST API directly —
this wrapper is a convenience, not a requirement.
"""

import json
import os
import sys

import httpx
from fastmcp import FastMCP

GATEWAY_URL = os.environ.get("JOBSEARCH_GATEWAY_URL", "http://localhost:8080")
API_TOKEN = os.environ.get("JOBSEARCH_API_TOKEN", "")

if not API_TOKEN:
    print("JOBSEARCH_API_TOKEN is not set", file=sys.stderr)

mcp = FastMCP("jobsearch")
_client = httpx.AsyncClient(
    base_url=GATEWAY_URL,
    headers={"Authorization": f"Bearer {API_TOKEN}"},
    timeout=30.0,
)


def _compact(jobs: list[dict]) -> str:
    lines = []
    for j in jobs:
        pub = (j.get("published") or "")[:10]
        lines.append(
            f"- [{j.get('id')}] {j.get('title')} | {j.get('company')} | "
            f"{(j.get('location') or {}).get('city')} | {pub} | {j.get('url')}"
        )
    return "\n".join(lines)


@mcp.tool
async def search_jobs(
    query: str,
    location: str = "",
    radius_km: int = 25,
    sources: list[str] | None = None,
    limit: int = 20,
    age_days: int | None = 30,
    persist: bool = True,
) -> str:
    """Search job postings. Returns a compact list (id, title, company,
    location, published, url) plus the full JSON result. Use get_job with one
    of the returned ids for full details.

    Args:
        query: job title / keyword (German works best, e.g. "Netzwerkadministrator")
        location: city, e.g. "München" (empty = Germany-wide for the
                 bundesagentur source)
        radius_km: search radius around location
        sources: subset of enabled sources (default: all enabled)
        limit: max results shown in the compact list
        age_days: only postings from the last N days (None = no filter)
        persist: store results in the gateway database
    """
    body = {
        "query": query,
        "location": location,
        "radius_km": radius_km,
        "size": max(limit, 25),
        "age_days": age_days,
        "persist": persist,
    }
    if sources:
        body["sources"] = sources
    resp = await _client.post("/v1/search", json=body)
    resp.raise_for_status()
    data = resp.json()
    status = "; ".join(
        f"{name}: {s['status']} ({s.get('count', 0)})"
        for name, s in data.get("sources", {}).items()
    )
    jobs = data.get("jobs", [])[:limit]
    header = (
        f"total: {data.get('total')} | duplicates removed: "
        f"{data.get('duplicates_removed')} | sources: {status}\n"
    )
    return header + _compact(jobs) + "\n\n--- raw json ---\n" + json.dumps(
        jobs, ensure_ascii=False, indent=1
    )


@mcp.tool
async def get_job(job_id: str) -> str:
    """Get full details for one job (use an id from search_jobs). Returns the
    description text as-is plus metadata; summarize or process it as needed.
    For searxng leads there is no structured description — follow the url."""
    resp = await _client.get(f"/v1/jobs/{job_id}")
    resp.raise_for_status()
    data = resp.json()
    header = (
        f"{data.get('title')} @ {data.get('company')} | {data.get('url')} "
        f"(source: {data.get('source')}, cached: {data.get('cached')})\n\n"
    )
    description = data.get("description") or "(no description available)"
    return header + description


@mcp.tool
async def list_sources() -> str:
    """List enabled job-search sources: name, tier (1=official API,
    2=meta-search, 3=scrape fallback), and whether each is healthy. Use this
    to degrade gracefully (e.g. drop a source from a search if unhealthy)."""
    resp = await _client.get("/v1/sources")
    resp.raise_for_status()
    return "\n".join(
        f"- {s['name']} (tier {s['tier']}): {'healthy' if s['healthy'] else 'UNHEALTHY'}"
        for s in resp.json()
    )


if __name__ == "__main__":
    mcp.run()