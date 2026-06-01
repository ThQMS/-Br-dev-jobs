import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Optional

import httpx
import structlog

_RUN_TIMEOUT = 300  # 5 minutes per scraper run

logger = structlog.get_logger(__name__)


@dataclass
class RawJob:
    """Raw job data as returned by a source, before normalisation."""

    source: str
    external_id: str
    title: str
    company: str
    url: str
    city: Optional[str] = None
    state: Optional[str] = None
    remote: bool = False
    contract_type_raw: Optional[str] = None
    salary_raw: Optional[str] = None
    description: Optional[str] = None


class BaseScraper(ABC):
    source_name: ClassVar[str]

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "br-dev-jobs/0.1 (job aggregator; contact thqueirozsilva@gmail.com)"},
            follow_redirects=True,
        )

    async def __aenter__(self) -> "BaseScraper":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    @abstractmethod
    async def fetch_jobs(self) -> list[RawJob]:
        """Fetch raw listings from the source. Called by run()."""

    async def run(self) -> list[RawJob]:
        """Public entry point. Wraps fetch_jobs() with timeout, logging, and error isolation."""
        log = logger.bind(source=self.source_name)
        t0 = time.monotonic()
        log.info("scraper_start")
        try:
            jobs = await asyncio.wait_for(self.fetch_jobs(), timeout=_RUN_TIMEOUT)
        except Exception as exc:
            elapsed = round(time.monotonic() - t0, 2)
            log.error("scraper_error", error=str(exc), elapsed_s=elapsed)
            return []
        elapsed = round(time.monotonic() - t0, 2)
        log.info("scraper_done", count=len(jobs), elapsed_s=elapsed)
        return jobs
