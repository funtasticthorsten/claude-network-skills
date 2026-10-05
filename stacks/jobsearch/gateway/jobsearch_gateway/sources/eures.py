"""EURES connector — Tier 1b, OPTIONAL and disabled by default.

The EURES (European labour market) API requires a free registration on the
EU developer portal — that conflicts with the zero-registration default of
this stack, so the connector only activates when JOBSEARCH_EURES_API_KEY is
set in .env.

The exact endpoint and auth header were NOT live-verified for this kit
(they need the registered key). This connector is therefore a documented
stub: it registers, reports its state honestly via health(), and returns a
clear error instead of guessing request shapes. Before enabling it:

  1. Check the current EURES API docs (https://europa.eu/!dPvxDf -> EURES
     REST API on the EU developer portal) for endpoint + auth header.
  2. Fill in ENDPOINT/AUTH below from one live request (same rule as the
     bundesagentur connector: map from reality, not from assumptions).
"""

from typing import Optional

from ..models import Job, SearchRequest
from ..throttle import SourceThrottle
from .base import SourceConnector


class EuresConnector(SourceConnector):
    name = "eures"
    tier = 1

    ENDPOINT = None  # TODO: set from EURES developer portal docs + one live request
    AUTH_HEADER = None  # TODO: e.g. ("Authorization", f"Bearer {settings.eures_api_key}")

    async def search(self, req: SearchRequest) -> list[Job]:
        if self.ENDPOINT is None:
            raise RuntimeError(
                "eures connector is a stub — set ENDPOINT/AUTH_HEADER from one "
                "live request first (see module docstring)"
            )
        raise NotImplementedError

    async def health(self) -> bool:
        return False  # not functional until the stub is completed

    async def get_details(self, job: Job) -> Optional[dict]:
        return None