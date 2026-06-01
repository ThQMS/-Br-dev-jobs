import structlog
import feedparser

from app.core.config import settings
from app.scrapers.base import BaseScraper, RawJob

logger = structlog.get_logger(__name__)

# Substrings that must appear in at least one tag for the entry to be kept
_TECH_KEYWORDS = frozenset({"dev", "engineer", "python", "javascript"})


def _has_tech_tag(tags: list[str]) -> bool:
    """Return True if any tag contains at least one of the target keywords."""
    for tag in tags:
        for kw in _TECH_KEYWORDS:
            if kw in tag:
                return True
    return False


class RemoteOKScraper(BaseScraper):
    source_name = "remoteok"

    async def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []

        try:
            resp = await self._http.get(settings.remoteok_rss_url)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("remoteok_fetch_error", error=str(exc))
            return jobs

        feed = feedparser.parse(resp.text)

        for entry in feed.entries:
            tags: list[str] = [t.term.lower() for t in getattr(entry, "tags", [])]

            if not _has_tech_tag(tags):
                continue

            external_id: str = entry.get("id") or entry.get("link", "")
            url: str = entry.get("link", "")
            if not url:
                continue

            jobs.append(
                RawJob(
                    source=self.source_name,
                    external_id=external_id,
                    title=entry.get("title", ""),
                    company=entry.get("author", ""),
                    remote=True,
                    description=entry.get("summary"),
                    url=url,
                )
            )

        return jobs
