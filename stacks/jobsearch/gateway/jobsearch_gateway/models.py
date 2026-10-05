"""Normalized pydantic schemas shared across the gateway."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Location(BaseModel):
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    raw: Optional[str] = None


class Salary(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: str = "EUR"
    raw: Optional[str] = None


class Job(BaseModel):
    id: str
    source: str
    refnr: Optional[str] = None
    title: str
    company: Optional[str] = None
    location: Location = Field(default_factory=Location)
    published: Optional[str] = None
    url: str
    details_url: Optional[str] = None
    salary: Salary = Field(default_factory=Salary)
    remote: bool = False
    employment_type: Optional[str] = None
    description_snippet: Optional[str] = None
    fetched_at: str = Field(default_factory=lambda: utcnow().isoformat())
    hash: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    location: str = ""
    radius_km: int = 25
    page: int = 1
    size: int = 25
    sources: Optional[list[str]] = None  # None = all enabled
    age_days: Optional[int] = None
    persist: bool = True


class SourceStatus(BaseModel):
    status: str  # "ok" | "error" | "skipped"
    count: int = 0
    duration_ms: int = 0
    cached: bool = False
    error: Optional[str] = None


class SearchResponse(BaseModel):
    jobs: list[Job]
    sources: dict[str, SourceStatus]
    total: int
    duplicates_removed: int


class JobDetails(BaseModel):
    id: str
    source: str
    title: Optional[str] = None
    company: Optional[str] = None
    url: str
    description: Optional[str] = None
    raw: dict = Field(default_factory=dict)  # source-specific normalized fields
    fetched_at: str = Field(default_factory=lambda: utcnow().isoformat())
    cached: bool = False