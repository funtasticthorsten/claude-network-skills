"""SQLite persistence (stdlib sqlite3 only — no ORM, no external DB).

Two tables:
  jobs         — normalized search results (payload = full Job JSON), dedup via unique hash
  job_details  — full detail fetches, TTL 24 h (purged lazily on access)

All writes go through an asyncio.Lock in main.py callers; every sqlite call
is run via asyncio.to_thread so the event loop never blocks.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  hash TEXT UNIQUE NOT NULL,
  source TEXT NOT NULL,
  payload TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_details (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen);
"""

DETAILS_TTL = timedelta(hours=24)


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- jobs ----------------------------------------------------------------

    def upsert_jobs(self, jobs: list[Job]) -> int:
        """Insert new jobs, refresh last_seen on known hashes. Returns rows written."""
        now = datetime.now(timezone.utc).isoformat()
        written = 0
        for job in jobs:
            payload = job.model_dump_json()
            cur = self._conn.execute(
                """
                INSERT INTO jobs (id, hash, source, payload, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET last_seen = excluded.last_seen
                """,
                (job.id, job.hash, job.source, payload, now, now),
            )
            written += cur.rowcount
        self._conn.commit()
        return written

    def list_jobs(
        self,
        q: str | None = None,
        source: str | None = None,
        since: str | None = None,
        remote: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        sql = "SELECT payload FROM jobs WHERE 1=1"
        params: list = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if since:
            sql += " AND last_seen >= ?"
            params.append(since)
        rows = self._conn.execute(
            sql + " ORDER BY last_seen DESC LIMIT ? OFFSET ?", (*params, limit, offset)
        ).fetchall()
        jobs = [Job.model_validate_json(r["payload"]) for r in rows]
        if q:
            q = q.lower()
            jobs = [
                j
                for j in jobs
                if q in j.title.lower()
                or (j.company and q in j.company.lower())
                or (j.description_snippet and q in j.description_snippet.lower())
            ]
        if remote:
            jobs = [j for j in jobs if j.remote]
        return jobs

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return Job.model_validate_json(row["payload"]) if row else None

    def job_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]

    # -- details ---------------------------------------------------------------

    def get_details(self, job_id: str) -> dict | None:
        """Return cached details payload if fresher than TTL, else None (and purge stale)."""
        row = self._conn.execute(
            "SELECT payload, fetched_at FROM job_details WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.now(timezone.utc) - fetched > DETAILS_TTL:
            self._conn.execute("DELETE FROM job_details WHERE id = ?", (job_id,))
            self._conn.commit()
            return None
        return json.loads(row["payload"])

    def save_details(self, job_id: str, payload: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO job_details (id, payload, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload,
                                          fetched_at = excluded.fetched_at
            """,
            (job_id, json.dumps(payload), now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()