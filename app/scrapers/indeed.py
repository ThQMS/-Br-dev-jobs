import asyncio
import random

import structlog
from playwright.async_api import Page, async_playwright

from app.core.config import settings
from app.scrapers.base import BaseScraper, RawJob

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://br.indeed.com/jobs?q=desenvolvedor&l=Brasil"
_MAX_JOBS = 200
_MAX_SCROLLS = 5
_CAPTCHA_SEL = "#captcha-box, .captcha-page, [data-testid='captcha']"

# Re-use LinkedIn's UA pool for consistency
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Card selectors (Indeed A/B tests these frequently; keep two fallbacks)
_CARD_SEL = ".job_seen_beacon, [data-testid='slider_item']"
_RESULTS_SEL = "#mosaic-provider-jobcards, [data-testid='jobsearch-ResultsList']"


async def _is_captcha(page: Page) -> bool:
    return await page.query_selector(_CAPTCHA_SEL) is not None


async def _scroll_to_load(page: Page, max_jobs: int, max_scrolls: int) -> None:
    for _ in range(max_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1.5, 3.0))
        count = len(await page.query_selector_all(_CARD_SEL))
        if count >= max_jobs:
            break


async def _extract_card(
    card: Page,
) -> tuple[str, str, str | None, str | None, str] | None:
    """Return (title, company, city, salary_raw, url) or None if card is incomplete."""
    title_el = await card.query_selector("[data-testid='jobTitle'] a, .jobTitle a")
    company_el = await card.query_selector("[data-testid='company-name'], .companyName")
    location_el = await card.query_selector("[data-testid='text-location'], .companyLocation")
    salary_el = await card.query_selector(
        ".salary-snippet, [class*='salaryText'], [data-testid='attribute_snippet_testid']"
    )

    title = (await title_el.inner_text()).strip() if title_el else ""
    company = (await company_el.inner_text()).strip() if company_el else ""
    city = (await location_el.inner_text()).strip() or None if location_el else None
    salary_raw = (await salary_el.inner_text()).strip() or None if salary_el else None
    url = await title_el.get_attribute("href") if title_el else ""

    if not title or not url:
        return None

    # Indeed card URLs are relative — make absolute
    if url and not url.startswith("http"):
        url = f"https://br.indeed.com{url}"

    return title, company, city, salary_raw, url


async def _fetch_description(page: Page, url: str) -> tuple[str | None, bool]:
    """Navigate to job detail page and return (description, captcha_detected)."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        if await _is_captcha(page):
            return None, True

        await asyncio.sleep(random.uniform(2, 5))

        desc_el = await page.query_selector("#jobDescriptionText, .jobDescriptionText")
        description = (await desc_el.inner_text()).strip() if desc_el else None
        return description, False
    except Exception:
        return None, False


class IndeedScraper(BaseScraper):
    source_name = "indeed"

    async def fetch_jobs(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen_ids: set[str] = set()

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
                await search_page.goto(_SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
                await search_page.wait_for_selector(_RESULTS_SEL, timeout=15_000)

                if await _is_captcha(search_page):
                    logger.warning("indeed_captcha_on_search")
                    return jobs

                await _scroll_to_load(search_page, _MAX_JOBS, _MAX_SCROLLS)

                # Collect card data first, then fetch descriptions
                card_data: list[tuple[str, str, str | None, str | None, str, str]] = []
                for card in await search_page.query_selector_all(_CARD_SEL):
                    # Extract job key (jk) for dedup — present on the card element
                    jk: str = await card.get_attribute("data-jk") or ""
                    if jk and jk in seen_ids:
                        continue
                    result = await _extract_card(card)
                    if not result:
                        continue
                    title, company, city, salary_raw, url = result
                    canonical_id = jk or url
                    if canonical_id in seen_ids:
                        continue
                    seen_ids.add(canonical_id)
                    card_data.append((title, company, city, salary_raw, url, canonical_id))

                logger.info("indeed_cards_collected", count=len(card_data))

                for i, (title, company, city, salary_raw, url, ext_id) in enumerate(card_data):
                    if i > 0:
                        await asyncio.sleep(random.uniform(2, 5))

                    description, captcha = await _fetch_description(detail_page, url)
                    if captcha:
                        logger.warning("indeed_captcha_on_detail", stopped_at=i)
                        break

                    jobs.append(
                        RawJob(
                            source=self.source_name,
                            external_id=ext_id,
                            title=title,
                            company=company,
                            city=city,
                            remote="remoto" in (city or "").lower(),
                            salary_raw=salary_raw,
                            description=description,
                            url=url,
                        )
                    )

            except Exception as exc:
                logger.error("indeed_scrape_error", error=str(exc))
            finally:
                await browser.close()

        return jobs
