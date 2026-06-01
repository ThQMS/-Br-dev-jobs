import asyncio
from typing import Any, cast

import httpx

from app.core.config import settings
from app.scrapers.base import BaseScraper, RawJob

_PAGE_SIZE = 100
_MAX_RETRIES = 3


async def _fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """GET url with up to _MAX_RETRIES attempts; exponential backoff on 5xx / network errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except httpx.HTTPStatusError as exc:
            # Don't retry client errors (4xx); always retry server errors (5xx)
            if exc.response.status_code < 500 or attempt == _MAX_RETRIES - 1:
                raise
        except httpx.RequestError:
            if attempt == _MAX_RETRIES - 1:
                raise
        await asyncio.sleep(2**attempt)  # 1 s, 2 s, 4 s
    raise RuntimeError("unreachable")  # for type-checker


def _parse_salary(salary: Any) -> str | None:
    """Convert a Gupy salaryRange value (dict or string) to a human-readable string."""
    if salary is None:
        return None
    if isinstance(salary, str):
        return salary or None
    if isinstance(salary, dict):
        lo: int | None = salary.get("minSalary")
        hi: int | None = salary.get("maxSalary")
        if lo and hi:
            return f"R$ {lo:,.0f}–R$ {hi:,.0f}".replace(",", ".")
        if lo:
            return f"R$ {lo:,.0f}+".replace(",", ".")
    return None


def _map_item(item: dict[str, Any]) -> RawJob | None:
    """Map a single Gupy API job object to RawJob; returns None when the URL is missing."""
    url: str = item.get("jobUrl", "")
    if not url:
        return None
    workplace: str = (item.get("workplaceType") or "").lower()
    return RawJob(
        source="gupy",
        external_id=str(item["id"]),
        title=item.get("name", ""),
        company=item.get("careerPageName", ""),
        city=item.get("city"),
        state=item.get("state"),
        remote=workplace in {"remote", "homeoffice", "home_office"},
        contract_type_raw=item.get("jobType"),
        salary_raw=_parse_salary(item.get("salaryRange")),
        description=None,  # full description requires a detail-page request
        url=url,
    )


class GupyScraper(BaseScraper):
    source_name = "gupy"

    async def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        page = 1

        while True:
            data = await _fetch_with_retry(
                self._http,
                settings.gupy_api_url,
                {"limit": _PAGE_SIZE, "jobTypes[]": "tech", "page": page},
            )
            for item in data.get("data", []):
                if raw := _map_item(item):
                    jobs.append(raw)

            # Gupy signals the last page by omitting or nulling pagination.next
            if not data.get("pagination", {}).get("next"):
                break
            page += 1

        return jobs
