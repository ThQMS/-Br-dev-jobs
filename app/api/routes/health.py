from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import RedisDep, SessionDep
from app.models.db import Job, JobSource
from app.models.schemas import CheckResult, HealthResponse, ScraperRunStatus, ScraperStatus

router = APIRouter(prefix="/health", tags=["health"])

_SOURCES = [s.value for s in JobSource]
# A scraper is considered "stale" if no new job has been seen in 2× the scrape interval
_STALE_THRESHOLD_HOURS = 14


async def _check_db(session: AsyncSession) -> CheckResult:
    try:
        await session.execute(text("SELECT 1"))
        return CheckResult(ok=True)
    except Exception as exc:
        return CheckResult(ok=False, detail=str(exc))


async def _check_redis(redis: object) -> CheckResult:
    try:
        await redis.ping()  # type: ignore[attr-defined]
        return CheckResult(ok=True)
    except Exception as exc:
        return CheckResult(ok=False, detail=str(exc))


async def _scraper_statuses(
    session: AsyncSession,
) -> list[ScraperStatus]:
    """Infer per-source health from the most recently created Job row per source."""
    now = datetime.now(tz=UTC)
    stale_cutoff = now - timedelta(hours=_STALE_THRESHOLD_HOURS)

    # Latest job creation time per source
    last_seen_rows = (
        await session.execute(
            sa.select(Job.source, sa.func.max(Job.created_at).label("latest")).group_by(Job.source)
        )
    ).fetchall()
    last_seen: dict[str, datetime] = {r.source: r.latest for r in last_seen_rows}

    # New jobs per source in the last 24h (proxy for "jobs_collected last run")
    recent_rows = (
        await session.execute(
            sa.select(Job.source, sa.func.count(Job.id).label("cnt"))
            .where(Job.created_at >= now - timedelta(hours=24))
            .group_by(Job.source)
        )
    ).fetchall()
    recent_counts: dict[str, int] = {r.source: r.cnt for r in recent_rows}

    statuses: list[ScraperStatus] = []
    for source in _SOURCES:
        latest = last_seen.get(source)
        if latest is None:
            run_status = ScraperRunStatus.idle
        elif latest < stale_cutoff:
            run_status = ScraperRunStatus.error
        else:
            run_status = ScraperRunStatus.success

        statuses.append(
            ScraperStatus(
                source=source,
                last_run=latest,
                jobs_collected=recent_counts.get(source, 0),
                status=run_status,
            )
        )
    return statuses


@router.get("", response_model=HealthResponse)
async def health(session: SessionDep, redis: RedisDep) -> HealthResponse:
    db_check, redis_check, scrapers = (
        await _check_db(session),
        await _check_redis(redis),
        await _scraper_statuses(session),
    )

    all_ok = db_check.ok and redis_check.ok
    overall = "healthy" if all_ok else "degraded"

    return HealthResponse(
        status=overall,
        checks={
            "database": db_check,
            "redis": redis_check,
        },
        scrapers=scrapers,
    )
