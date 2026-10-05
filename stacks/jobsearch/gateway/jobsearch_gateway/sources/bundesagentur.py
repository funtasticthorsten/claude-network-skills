"""Bundesagentur für Arbeit jobsuche connector — Tier 1, the core source.

API: https://jobsuche.api.bund.dev/ (unofficial, reverse-engineered from the
official mobile app; endpoints may change without notice).

Field mapping was built from a LIVE v6 response on 2026-10-05:
  ergebnisliste[].stellenangebotsTitel    -> title
  ergebnisliste[].firma                   -> company
  ergebnisliste[].referenznummer          -> refnr
  ergebnisliste[].stellenlokationen[0].adresse (ort/plz/region/land) -> location
  ergebnisliste[].datumErsteVeroeffentlichung / veroeffentlichungszeitraum.von -> published
  ergebnisliste[].homeofficemoeglich      -> remote
  ergebnisliste[].arbeitszeitVollzeit     -> employment_type (VOLLZEIT)
  details: pc/v4/jobdetails/{base64(refnr)} -> stellenangebotsBeschreibung (full text)
"""

import base64
from typing import Optional

import httpx

from ..config import settings
from ..models import Job, Location, Salary, SearchRequest
from ..throttle import SourceThrottle
from .base import SourceConnector

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
# Public client key documented in the bundesAPI/jobsuche-api repo — not a secret.
API_KEY = "jobboerse-jobsuche"

REQUEST_TIMEOUT_S = 10.0
COUNTRY_MAP = {"DEUTSCHLAND": "DE", "ÖSTERREICH": "AT", "SCHWEIZ": "CH"}


class BundesagenturConnector(SourceConnector):
    name = "bundesagentur"
    tier = 1

    def __init__(self, throttle: SourceThrottle):
        super().__init__(throttle)
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            headers={
                "X-API-Key": API_KEY,
                "Accept": "application/json",
                "User-Agent": settings.user_agent,
            },
        )

    async def search(self, req: SearchRequest) -> list[Job]:
        params: dict = {
            "was": req.query,
            "page": req.page,
            "size": min(req.size, 100),
            "umkreis": req.radius_km if req.location else 0,
        }
        if req.location:
            params["wo"] = req.location
        if req.age_days is not None:
            params["veroeffentlichtseit"] = min(req.age_days, 100)

        async with self.throttle:
            resp = await self._client.get(f"{BASE}/v6/jobs", params=params)
            resp.raise_for_status()
            data = resp.json()
        return [self._map_position(p) for p in data.get("ergebnisliste", [])]

    async def get_details(self, job: Job) -> Optional[dict]:
        if job.source != self.name or not job.refnr:
            return None
        ref_b64 = base64.b64encode(job.refnr.encode()).decode()
        async with self.throttle:
            resp = await self._client.get(f"{BASE}/v4/jobdetails/{ref_b64}")
            resp.raise_for_status()
            data = resp.json()
        return {
            "title": data.get("stellenangebotsTitel") or job.title,
            "company": data.get("firma") or job.company,
            "url": job.url,
            "description": data.get("stellenangebotsBeschreibung"),
            "published": data.get("datumErsteVeroeffentlichung"),
            "raw": data,
        }

    async def health(self) -> bool:
        try:
            async with self.throttle:
                resp = await self._client.get(
                    f"{BASE}/v6/jobs", params={"was": "test", "size": 1}
                )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # -- mapping ----------------------------------------------------------------

    def _map_position(self, p: dict) -> Job:
        refnr = p.get("referenznummer") or ""
        loc = Location()
        locations = p.get("stellenlokationen") or []
        if locations:
            addr = locations[0].get("adresse", {})
            loc = Location(
                city=addr.get("ort"),
                postal_code=addr.get("plz"),
                country=COUNTRY_MAP.get(addr.get("land", ""), addr.get("land")),
                raw=", ".join(
                    x for x in [addr.get("plz"), addr.get("ort"), addr.get("region")] if x
                ),
            )
        employment = "VOLLZEIT" if p.get("arbeitszeitVollzeit") else None
        return Job(
            id=f"{self.name}:{refnr}",
            source=self.name,
            refnr=refnr,
            title=p.get("stellenangebotsTitel") or "",
            company=p.get("firma"),
            location=loc,
            published=p.get("veroeffentlichungszeitraum", {}).get("von")
            or p.get("datumErsteVeroeffentlichung"),
            url=f"https://www.arbeitsagentur.de/jobsuche/detail/{refnr}",
            salary=Salary(
                raw=None
                if p.get("verguetungsangabe") in (None, "KEINE_ANGABEN")
                else p.get("verguetungsangabe")
            ),
            remote=bool(p.get("homeofficemoeglich")),
            employment_type=employment,
        )