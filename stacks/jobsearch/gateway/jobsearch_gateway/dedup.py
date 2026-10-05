"""Cross-source deduplication.

hash = first 16 hex chars of sha256 over source, refnr (or URL), title and
company — so the same posting seen twice in one search (or persisted and
re-fetched) collapses into one row. The hash doubles as the SQLite unique key.
"""

import hashlib

from .models import Job


def job_hash(job: Job) -> str:
    key = "|".join(
        [
            job.source,
            (job.refnr or job.url).lower(),
            (job.title or "").casefold().strip(),
            (job.company or "").casefold().strip(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def with_hashes(jobs: list[Job]) -> list[Job]:
    for job in jobs:
        if not job.hash:
            job.hash = job_hash(job)
    return jobs


def dedup(jobs: list[Job]) -> tuple[list[Job], int]:
    """Drop jobs with an already-seen hash within this list. Returns (unique, removed)."""
    seen: dict[str, Job] = {}
    removed = 0
    for job in jobs:
        if job.hash in seen:
            removed += 1
            continue
        seen[job.hash] = job
    return list(seen.values()), removed