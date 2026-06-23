import json
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import RedisDep, SessionDep
from app.models.db import DailySnapshot, Job
from app.models.schemas import (
    InsightsCity,
    InsightsDashboard,
    InsightsSalary,
    InsightsTechStack,
    SalariesResponse,
    SalaryByTech,
)

router = APIRouter(prefix="/insights", tags=["insights"])

_DASHBOARD_KEY = "insights:dashboard"
_TECH_KEY = "insights:technologies"
_SALARY_KEY = "insights:salaries"
_CACHE_1H = 3600


# ── Shared query helpers ──────────────────────────────────────────────────────


async def _top_technologies(
    session: AsyncSession,
    limit: int = 20,
    *,
    since: datetime | None = None,
) -> list[tuple[str, int]]:
    """Return [(tech, count)] for active jobs, optionally filtered by created_at >= since."""
    where = "is_active = true"
    if since:
        where += f" AND created_at >= '{since.isoformat()}'"
    rows = (
        await session.execute(
            text(f"""
                SELECT tech, COUNT(*) AS cnt
                FROM jobs, unnest(technologies) AS tech
                WHERE {where}
                GROUP BY tech
                ORDER BY cnt DESC
                LIMIT {limit}
            """)
        )
    ).fetchall()
    return [(r.tech, r.cnt) for r in rows]


async def _salary_by_seniority(session: AsyncSession) -> list[InsightsSalary]:
    rows = (
        await session.execute(
            text("""
                SELECT
                    seniority,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary_min)::int AS median,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY salary_min)::int AS p25,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY salary_min)::int AS p75,
                    COUNT(*) AS sample_size
                FROM jobs
                WHERE is_active = true AND salary_min IS NOT NULL
                GROUP BY seniority
                ORDER BY median DESC NULLS LAST
            """)
        )
    ).fetchall()
    return [
        InsightsSalary(
            seniority=r.seniority,
            median=r.median or 0,
            p25=r.p25 or 0,
            p75=r.p75 or 0,
            sample_size=r.sample_size,
        )
        for r in rows
    ]


# ── GET /insights (dashboard) ─────────────────────────────────────────────────


@router.get("", response_model=InsightsDashboard)
async def get_dashboard(session: SessionDep, redis: RedisDep) -> InsightsDashboard:
    cached = await redis.get(_DASHBOARD_KEY)
    if cached:
        return InsightsDashboard(**json.loads(cached))

    now_utc = datetime.now(tz=UTC)
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=UTC)
    week_start = now_utc - timedelta(days=7)

    # ── Counts ────────────────────────────────────────────────────────────────
    total_active: int = (
        await session.execute(sa.select(sa.func.count(Job.id)).where(Job.is_active.is_(True)))
    ).scalar_one()

    new_today: int = (
        await session.execute(
            sa.select(sa.func.count(Job.id)).where(Job.first_seen_at >= today_start)
        )
    ).scalar_one()

    new_this_week: int = (
        await session.execute(
            sa.select(sa.func.count(Job.id)).where(Job.first_seen_at >= week_start)
        )
    ).scalar_one()

    # ── Top technologies ──────────────────────────────────────────────────────
    total_for_pct = max(total_active, 1)
    tech_pairs = await _top_technologies(session, limit=20)
    # Trend: compare current week vs previous week counts
    prev_week = now_utc - timedelta(days=14)
    prev_rows = (
        await session.execute(
            text("""
                SELECT tech, COUNT(*) AS cnt
                FROM jobs, unnest(technologies) AS tech
                WHERE created_at >= :prev AND created_at < :curr
                GROUP BY tech
            """),
            {"prev": prev_week.isoformat(), "curr": week_start.isoformat()},
        )
    ).fetchall()
    prev_map = {r.tech: r.cnt for r in prev_rows}

    curr_week_rows = (
        await session.execute(
            text("""
                SELECT tech, COUNT(*) AS cnt
                FROM jobs, unnest(technologies) AS tech
                WHERE created_at >= :curr
                GROUP BY tech
            """),
            {"curr": week_start.isoformat()},
        )
    ).fetchall()
    curr_map = {r.tech: r.cnt for r in curr_week_rows}

    top_technologies = [
        InsightsTechStack(
            technology=tech,
            count=cnt,
            percentage=round(cnt / total_for_pct * 100, 1),
            trend=round(
                (curr_map.get(tech, 0) - prev_map.get(tech, 0))
                / max(prev_map.get(tech, 1), 1)
                * 100,
                1,
            ),
        )
        for tech, cnt in tech_pairs
    ]

    # ── Salary by seniority ───────────────────────────────────────────────────
    salary_by_seniority = await _salary_by_seniority(session)

    # ── Top cities ────────────────────────────────────────────────────────────
    city_rows = (
        await session.execute(
            text("""
                SELECT
                    city,
                    COALESCE(state, '') AS state,
                    COUNT(*) AS count,
                    ROUND(
                        100.0 * COUNT(CASE WHEN remote = true THEN 1 END) / COUNT(*),
                        1
                    ) AS remote_pct
                FROM jobs
                WHERE is_active = true AND city IS NOT NULL
                GROUP BY city, state
                ORDER BY count DESC
                LIMIT 15
            """)
        )
    ).fetchall()
    top_cities = [
        InsightsCity(
            city=r.city,
            state=r.state,
            count=r.count,
            remote_percentage=float(r.remote_pct),
        )
        for r in city_rows
    ]

    # ── Daily volume (last 30 days from DailySnapshot) ────────────────────────
    snap_rows = (
        await session.execute(
            sa.select(DailySnapshot.date, DailySnapshot.total_jobs, DailySnapshot.new_jobs)
            .where(DailySnapshot.date >= date.today() - timedelta(days=30))
            .order_by(DailySnapshot.date)
        )
    ).fetchall()
    daily_volume = [
        {"date": str(r.date), "total": r.total_jobs, "new": r.new_jobs} for r in snap_rows
    ]

    response = InsightsDashboard(
        total_active_jobs=total_active,
        new_today=new_today,
        new_this_week=new_this_week,
        top_technologies=top_technologies,
        salary_by_seniority=salary_by_seniority,
        top_cities=top_cities,
        daily_volume=daily_volume,
        last_updated=now_utc,
    )
    await redis.setex(_DASHBOARD_KEY, _CACHE_1H, response.model_dump_json())
    return response


# ── GET /insights/technologies ────────────────────────────────────────────────


@router.get("/technologies", response_model=list[InsightsTechStack])
async def get_technologies(session: SessionDep, redis: RedisDep) -> list[InsightsTechStack]:
    """Top 50 technologies with trend vs previous week."""
    cached = await redis.get(_TECH_KEY)
    if cached:
        return [InsightsTechStack(**item) for item in json.loads(cached)]

    now_utc = datetime.now(tz=UTC)
    week_ago = now_utc - timedelta(days=7)
    two_weeks_ago = now_utc - timedelta(days=14)

    rows = (
        await session.execute(
            text("""
                WITH total AS (
                    SELECT COUNT(*) AS n FROM jobs WHERE is_active = true
                ),
                curr AS (
                    SELECT tech, COUNT(*) AS cnt
                    FROM jobs, unnest(technologies) AS tech
                    WHERE is_active = true AND created_at >= :week_ago
                    GROUP BY tech
                ),
                prev AS (
                    SELECT tech, COUNT(*) AS cnt
                    FROM jobs, unnest(technologies) AS tech
                    WHERE created_at >= :two_weeks_ago
                      AND created_at < :week_ago
                    GROUP BY tech
                ),
                all_tech AS (
                    SELECT tech, COUNT(*) AS total_cnt
                    FROM jobs, unnest(technologies) AS tech
                    WHERE is_active = true
                    GROUP BY tech
                    ORDER BY total_cnt DESC
                    LIMIT 50
                )
                SELECT
                    a.tech,
                    a.total_cnt,
                    COALESCE(c.cnt, 0) AS curr_cnt,
                    COALESCE(p.cnt, 0) AS prev_cnt,
                    t.n AS total_jobs
                FROM all_tech a
                LEFT JOIN curr c ON c.tech = a.tech
                LEFT JOIN prev p ON p.tech = a.tech
                CROSS JOIN total t
                ORDER BY a.total_cnt DESC
            """),
            {"week_ago": week_ago.isoformat(), "two_weeks_ago": two_weeks_ago.isoformat()},
        )
    ).fetchall()

    total_jobs = rows[0].total_jobs if rows else 1
    result = [
        InsightsTechStack(
            technology=r.tech,
            count=r.total_cnt,
            percentage=round(r.total_cnt / max(total_jobs, 1) * 100, 1),
            trend=round((r.curr_cnt - r.prev_cnt) / max(r.prev_cnt, 1) * 100, 1),
        )
        for r in rows
    ]
    await redis.setex(_TECH_KEY, _CACHE_1H, json.dumps([i.model_dump() for i in result]))
    return result


# ── GET /insights/salaries ────────────────────────────────────────────────────


@router.get("/salaries", response_model=SalariesResponse)
async def get_salaries(session: SessionDep, redis: RedisDep) -> SalariesResponse:
    """Salary distribution by seniority and top technologies (min 5 data points per group)."""
    cached = await redis.get(_SALARY_KEY)
    if cached:
        return SalariesResponse(**json.loads(cached))

    by_seniority = await _salary_by_seniority(session)

    by_tech_rows = (
        await session.execute(
            text("""
                WITH tech_salary AS (
                    SELECT tech, salary_min
                    FROM jobs, unnest(technologies) AS tech
                    WHERE is_active = true AND salary_min IS NOT NULL
                )
                SELECT
                    tech AS technology,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary_min)::int AS median,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY salary_min)::int AS p25,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY salary_min)::int AS p75,
                    COUNT(*) AS sample_size
                FROM tech_salary
                GROUP BY tech
                HAVING COUNT(*) >= 5
                ORDER BY median DESC NULLS LAST
                LIMIT 20
            """)
        )
    ).fetchall()

    by_technology = [
        SalaryByTech(
            technology=r.technology,
            median=r.median or 0,
            p25=r.p25 or 0,
            p75=r.p75 or 0,
            sample_size=r.sample_size,
        )
        for r in by_tech_rows
    ]

    response = SalariesResponse(by_seniority=by_seniority, by_technology=by_technology)
    await redis.setex(_SALARY_KEY, _CACHE_1H, response.model_dump_json())
    return response
