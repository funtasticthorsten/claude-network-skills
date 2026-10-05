"""Per-source polite throttling: a semaphore (one in-flight request per source)
plus a minimum interval between requests. No Redis, no external limiter."""

import asyncio
import time


class SourceThrottle:
    def __init__(self, min_interval_s: float):
        self._min_interval = min_interval_s
        self._sem = asyncio.Semaphore(1)
        self._last_release = 0.0

    async def __aenter__(self) -> "SourceThrottle":
        await self._sem.acquire()
        wait = self._last_release + self._min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc) -> None:
        self._last_release = time.monotonic()
        self._sem.release()