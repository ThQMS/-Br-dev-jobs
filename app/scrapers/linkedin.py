import asyncio
import random
import re

import structlog
from playwright.async_api import Page, async_playwright

from app.core.config import settings
from app.scrapers.base import BaseScraper, RawJob

logger = structlog.get_logger(__name__)

_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords=developer&location=Brasil&f_TP=1,2"
)
_MAX_JOBS = 200
_MAX_SCROLLS = 5
_CAPTCHA_SEL = ".captcha__title, #captcha-internal, .challenge-page"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


async def _is_captcha(page: Page) -> bool:
    return await page.query_selector(_CAPTCHA_SEL) is not None


async def _scroll_to_load(page: Page, max_jobs: int, max_scrolls: int) -> None:
    """Scroll the search results until max_jobs cards are loaded or max_scrolls reached."""
    for _ in range(max_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2.5)
        count = len(await page.query_selector_all(".job-search-card"))
        if count >= max_jobs:
            break


async def _extract_card(card: Page) -> tuple[str, str, str | None, str] | None:
    """Return (title, company, city, url) from a search-result card, or None if incomplete."""
    title_el = await card.query_selector(".base-search-card__title")
    company_el = await card.query_selector(".base-search-card__subtitle")
    location_el = await card.query_selector(".job-search-card__location")
    link_el = await card.query_selector("a.base-card__full-link")

    title = (await title_el.inner_text()).strip() if title_el else ""
    company = (await company_el.inner_text()).strip() if company_el else ""
    city = (await location_el.inner_text()).strip() or None if location_el else None
    url = await link_el.get_attribute("href") if link_el else ""

    if not title or not url:
        return None
    return title, company, city, url


async def _fetch_detail(
    page: Page, url: str
) -> tuple[str | None, str | None, bool]:
    """Navigate to a job detail page.

    Returns (description, salary_raw, captcha_detected).
    Sleeps 2–5 s after page load to mimic human reading pace.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        if await _is_captcha(page):
            return None, None, True

        await asyncio.sleep(random.uniform(2, 5))

        desc_el = await page.query_selector(".description__text")
        description = (await desc_el.inner_text()).strip() if desc_el else None

        salary_el = await page.query_selector(
            ".salary-snippet-container, .compensation__list"
        )
        salary = (await salary_el.inner_text()).strip() if salary_el else None

        return description, salary, False
    except Exception:
        return None, None, False


class LinkedInScraper(BaseScraper):
    source_name = "linkedin"

    async def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=settings.playwright_headless)
            context = await browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
            )
            search_page = await context.new_page()
            detail_page = await context.new_page()

            try:
                await search_page.goto(
                    _SEARCH_URL, wait_until="domcontentloaded", timeout=30_000
                )
                await search_page.wait_for_selector(".job-search-card", timeout=15_000)

                if await _is_captcha(search_page):
                    logger.warning("linkedin_captcha_on_search")
                    return jobs

                await _scroll_to_load(search_page, _MAX_JOBS, _MAX_SCROLLS)

                # Collect card metadata before leaving the search page
                card_data: list[tuple[str, str, str | None, str]] = []
                for card in await search_page.query_selector_all(".job-search-card"):
                    result = await _extract_card(card)
                    if result:
                        card_data.append(result)

                logger.info("linkedin_cards_collected", count=len(card_data))

                # Fetch detail pages one by one with rate-limiting sleep
                for i, (title, company, city, url) in enumerate(card_data):
                    if i > 0:
                        await asyncio.sleep(random.uniform(2, 5))

                    description, salary_raw, captcha = await _fetch_detail(
                        detail_page, url
                    )
                    if captcha:
                        logger.warning("linkedin_captcha_on_detail", stopped_at=i)
                        break

                    match = re.search(r"(\d{8,})", url)
                    external_id = match.group(1) if match else url

                    jobs.append(
                        RawJob(
                            source=self.source_name,
                            external_id=external_id,
                            title=title,
                            company=company,
                            city=city,
                            remote="remoto" in (city or "").lower(),
                            description=description,
                            salary_raw=salary_raw,
                            url=url,
                        )
                    )

            except Exception as exc:
                logger.error("linkedin_scrape_error", error=str(exc))
            finally:
                await browser.close()

        return jobs
