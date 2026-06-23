from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scrapers.remoteok import RemoteOKScraper, _has_tech_tag


def _make_entry(
    *,
    title: str = "Python Dev",
    link: str = "https://remoteok.com/1",
    entry_id: str = "1",
    author: str = "ACME",
    summary: str = "Python developer role",
    tags: list[str] | None = None,
) -> MagicMock:
    entry = MagicMock()
    entry.get = lambda k, d="": {
        "title": title,
        "link": link,
        "id": entry_id,
        "author": author,
        "summary": summary,
    }.get(k, d)
    entry.tags = [MagicMock(term=t) for t in (tags or [])]
    return entry


def _mock_scraper(entries: list[MagicMock]) -> tuple[RemoteOKScraper, MagicMock]:
    mock_feed = MagicMock()
    mock_feed.entries = entries
    scraper = RemoteOKScraper()
    scraper._http = AsyncMock()  # type: ignore[method-assign]
    scraper._http.get.return_value = MagicMock(text="", raise_for_status=lambda: None)
    return scraper, mock_feed


# ── _has_tech_tag unit tests ──────────────────────────────────────────────────


def test_has_tech_tag_python() -> None:
    assert _has_tech_tag(["python", "aws"]) is True


def test_has_tech_tag_dev_substring() -> None:
    assert _has_tech_tag(["frontend-dev", "react"]) is True


def test_has_tech_tag_engineer() -> None:
    assert _has_tech_tag(["senior-engineer"]) is True


def test_has_tech_tag_javascript() -> None:
    assert _has_tech_tag(["javascript", "node"]) is True


def test_has_tech_tag_no_match() -> None:
    assert _has_tech_tag(["design", "ux", "figma"]) is False


def test_has_tech_tag_empty() -> None:
    assert _has_tech_tag([]) is False


# ── fetch_jobs integration tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_feed_returns_empty() -> None:
    scraper, mock_feed = _mock_scraper([])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_python_tag_is_included() -> None:
    scraper, mock_feed = _mock_scraper([_make_entry(tags=["python", "remote"])])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert len(jobs) == 1
    assert jobs[0].remote is True
    assert jobs[0].source == "remoteok"
    assert jobs[0].external_id == "1"


@pytest.mark.asyncio
async def test_javascript_tag_is_included() -> None:
    scraper, mock_feed = _mock_scraper([_make_entry(tags=["javascript", "react"])])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_no_tech_tag_is_excluded() -> None:
    scraper, mock_feed = _mock_scraper([_make_entry(tags=["design", "ux"])])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_entry_without_tags_is_excluded() -> None:
    scraper, mock_feed = _mock_scraper([_make_entry(tags=[])])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_missing_url_is_skipped() -> None:
    scraper, mock_feed = _mock_scraper([_make_entry(link="", tags=["python"])])
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert jobs == []


@pytest.mark.asyncio
async def test_multiple_entries_mixed_tags() -> None:
    entries = [
        _make_entry(entry_id="1", tags=["python"]),
        _make_entry(entry_id="2", tags=["design"]),
        _make_entry(entry_id="3", tags=["engineer", "go"]),
    ]
    scraper, mock_feed = _mock_scraper(entries)
    with patch("app.scrapers.remoteok.feedparser.parse", return_value=mock_feed):
        jobs = await scraper.fetch_jobs()
    assert len(jobs) == 2
    assert {j.external_id for j in jobs} == {"1", "3"}
