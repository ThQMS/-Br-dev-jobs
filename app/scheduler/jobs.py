import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NormalizationError
from app.etl.deduplicator import is_duplicate
from app.etl.enricher import JobEnricher
from app.etl.normalizer import normalize
from app.models.db import DailySnapshot, Job, Seniority, async_session_factory
from app.scrapers.base import RawJob
from app.scrapers.gupy import GupyScraper
from app.scrapers.indeed import IndeedScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.remoteok import RemoteOKScraper

logger = structlog.get_logger(__name__)

_SCRAPERS = [GupyScraper, LinkedInScraper, IndeedScraper, RemoteOKScraper]
_STALE_DAYS = 7
_enricher = JobEnricher()


# ── Per-job helpers ───────────────────────────────────────────────────────────


async def _touch_last_seen(session: AsyncSession, content_hash: str) -> None:
    """Bump last_seen_at on an existing job so it doesn't get expired."""
    await session.execute(
        sa.update(Job)
        .where(Job.content_hash == content_hash)
        .values(last_seen_at=datetime.now(tz=UTC))
    )
    await session.commit()


# ── Scraping phase ────────────────────────────────────────────────────────────


async def _run_scrapers() -> list[RawJob]:
    """Run all scrapers concurrently up to MAX_CONCURRENT_SCRAPERS at a time."""
    sem = asyncio.Semaphore(settings.max_concurrent_scrapers)

    async def _one(cls: Any) -> list[RawJob]:
        async with sem, cls() as scraper:
            return await scraper.run()  # never raises — returns [] on error

    batches = await asyncio.gather(*[_one(cls) for cls in _SCRAPERS])
    return [job for batch in batches for job in batch]


# ── ETL phase ─────────────────────────────────────────────────────────────────


async def _process_raw(
    session: AsyncSession,
    raw_jobs: list[RawJob],
    log: Any,
) -> tuple[int, int, int]:
    """Normalise → dedup → enrich → insert each RawJob.

    Returns (new_count, updated_count, error_count).
    """
    new = updated = errors = 0

    for raw in raw_jobs:
        # a. Normalise (also computes content_hash internally via compute_hash)
        try:
            normalized = normalize(raw)
        except NormalizationError as exc:
            log.warning("normalize_failed", url=raw.url, error=str(exc))
            errors += 1
            continue

        # b. content_hash already computed inside normalize()
        h = normalized.content_hash

        try:
            # c. Dedup check — if already exists, just refresh last_seen_at
            if await is_duplicate(session, h):
                await _touch_last_seen(session, h)
                updated += 1
                continue

            # d. Extract technologies via NLP
            technologies = _enricher.extract_technologies(
                f"{normalized.title} {normalized.raw_description or ''}"
            )

            # e. INSERT
            session.add(
                Job(
                    external_id=normalized.external_id,
                    source=normalized.source,
                    title=normalized.title,
                    company=normalized.company,
                    city=normalized.city,
                    state=normalized.state,
                    remote=normalized.remote,
                    contract_type=normalized.contract_type,
                    seniority=normalized.seniority,
                    salary_min=normalized.salary_min,
                    salary_max=normalized.salary_max,
                    technologies=technologies,
                    raw_description=normalized.raw_description,
                    url=normalized.url,
                    content_hash=h,
                )
            )
            await session.commit()
            new += 1

        except IntegrityError:
            # URL unique-constraint fired — someone else inserted it in the same run
            await session.rollback()
            await _touch_last_seen(session, h)
            updated += 1

        except Exception as exc:
            await session.rollback()
            log.error("save_failed", url=normalized.url, error=str(exc))
            errors += 1

    return new, updated, errors


# ── Post-processing ───────────────────────────────────────────────────────────


async def _expire_stale_jobs(session: AsyncSession) -> int:
    """Set is_active=False on jobs not seen for > _STALE_DAYS days. Returns row count."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=_STALE_DAYS)
    result = await session.execute(
        sa.update(Job)
        .where(Job.is_active.is_(True), Job.last_seen_at < cutoff)
        .values(is_active=False)
    )
    await session.commit()
    return result.rowcount  # type: ignore[return-value]


async def _generate_snapshot(
    session: AsyncSession,
    new_jobs: int,
    expired_jobs: int,
) -> None:
    """Upsert today's DailySnapshot with aggregated metrics."""
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=UTC)

    # Total active jobs
    total_active: int = (
        await session.execute(sa.select(sa.func.count(Job.id)).where(Job.is_active.is_(True)))
    ).scalar_one()

    # New jobs added today (across all runs)
    new_today: int = (
        await session.execute(
            sa.select(sa.func.count(Job.id)).where(Job.first_seen_at >= today_start)
        )
    ).scalar_one()

    # Top technologies via PostgreSQL unnest
    tech_rows = (
        await session.execute(
            text("""
                SELECT tech, COUNT(*) AS cnt
                FROM jobs, unnest(technologies) AS tech
                WHERE is_active = true
                GROUP BY tech
                ORDER BY cnt DESC
                LIMIT 20
            """)
        )
    ).fetchall()
    top_technologies: dict[str, int] = {r.tech: r.cnt for r in tech_rows}

    # Top cities
    city_rows = (
        await session.execute(
            sa.select(Job.city, sa.func.count(Job.id).label("cnt"))
            .where(Job.is_active.is_(True), Job.city.is_not(None))
            .group_by(Job.city)
            .order_by(sa.func.count(Job.id).desc())
            .limit(20)
        )
    ).fetchall()
    top_cities: dict[str, int] = {r.city: r.cnt for r in city_rows}

    # Average salary_min by seniority
    salary_rows = (
        await session.execute(
            sa.select(
                Job.seniority,
                sa.func.round(sa.func.avg(sa.cast(Job.salary_min, sa.Float))).label("avg"),
            )
            .where(Job.is_active.is_(True), Job.salary_min.is_not(None))
            .group_by(Job.seniority)
        )
    ).fetchall()
    salary_map: dict[str, int] = {r.seniority: int(r.avg) for r in salary_rows}

    snapshot_values: dict[str, Any] = {
        "date": today,
        "total_jobs": total_active,
        "new_jobs": new_today,
        "expired_jobs": expired_jobs,
        "top_technologies": top_technologies,
        "top_cities": top_cities,
        "avg_salary_junior": salary_map.get(Seniority.junior),
        "avg_salary_mid": salary_map.get(Seniority.mid),
        "avg_salary_senior": salary_map.get(Seniority.senior),
    }
    await session.execute(
        pg_insert(DailySnapshot)
        .values(**snapshot_values)
        .on_conflict_do_update(index_elements=["date"], set_=snapshot_values)
    )
    await session.commit()


async def _invalidate_cache() -> int:
    """Delete all Redis keys matching 'insights:*'. Returns number of keys removed."""
    client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        keys: list[str] = await client.keys("insights:*")
        if not keys:
            return 0
        return await client.delete(*keys)
    finally:
        await client.aclose()


# ── Full pipeline ─────────────────────────────────────────────────────────────


async def run_full_pipeline() -> None:
    log = logger.bind(pipeline="full")
    t0 = time.monotonic()
    log.info("pipeline_start")

    # 1-2: Scrape all sources in parallel
    all_raw = await _run_scrapers()
    log.info("pipeline_scraped", total_raw=len(all_raw))

    # 3: Normalise → dedup → enrich → persist
    new_count = updated_count = error_count = 0
    async with async_session_factory() as session:
        new_count, updated_count, error_count = await _process_raw(session, all_raw, log)

        # 4: Expire jobs not seen in the last _STALE_DAYS days
        expired_count = await _expire_stale_jobs(session)

        # 5: Upsert today's DailySnapshot
        await _generate_snapshot(session, new_count, expired_count)

    # 6: Invalidate insights cache
    cache_cleared = await _invalidate_cache()

    # 7: Summary log
    elapsed = round(time.monotonic() - t0, 2)
    log.info(
        "pipeline_done",
        new=new_count,
        updated=updated_count,
        expired=expired_count,
        errors=error_count,
        cache_keys_cleared=cache_cleared,
        elapsed_s=elapsed,
    )


# ── Scheduler factory ─────────────────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        run_full_pipeline,
        trigger=IntervalTrigger(hours=settings.scrape_interval_hours, timezone=UTC),
        id="pipeline_interval",
        name="Job scraping pipeline",
        replace_existing=True,
        # next_run_time=now() triggers one immediate run on scheduler.start()
        next_run_time=datetime.now(tz=UTC),
    )
    return scheduler
