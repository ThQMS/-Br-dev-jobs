import enum
import hashlib
import json
import uuid
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.dialects.postgresql import array as pg_array

from app.api.deps import RedisDep, SessionDep
from app.core.config import settings
from app.models.db import ContractType, Job, JobSource, Seniority
from app.models.schemas import JobDetailResponse, JobListResponse, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


class SortOrder(str, enum.Enum):
    recent = "recent"
    salary_asc = "salary_asc"
    salary_desc = "salary_desc"


def _list_cache_key(params: dict) -> str:
    digest = hashlib.md5(
        json.dumps(params, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"jobs:list:{digest}"


@router.get("", response_model=JobListResponse)
async def list_jobs(
    session: SessionDep,
    redis: RedisDep,
    # Fulltext
    q: Optional[str] = Query(None, description="Search in title and company"),
    # Location
    city: Optional[str] = None,
    state: Optional[str] = None,
    # Work style
    remote: Optional[bool] = None,
    # Classification
    seniority: Optional[Seniority] = None,
    contract_type: Optional[ContractType] = None,
    source: Optional[JobSource] = None,
    # Skills — ANY match (job must have at least one of the listed technologies)
    technologies: list[str] = Query(default=[]),
    # Salary range (both in BRL)
    salary_min: Optional[int] = Query(None, ge=0),
    salary_max: Optional[int] = Query(None, ge=0),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Ordering
    sort: SortOrder = SortOrder.recent,
) -> JobListResponse:
    # ── Cache lookup ──────────────────────────────────────────────────────────
    cache_params = dict(
        q=q, city=city, state=state, remote=remote,
        seniority=seniority, contract_type=contract_type, source=source,
        technologies=sorted(technologies), salary_min=salary_min, salary_max=salary_max,
        page=page, page_size=page_size, sort=sort,
    )
    cache_key = _list_cache_key(cache_params)
    cached = await redis.get(cache_key)
    if cached:
        return JobListResponse(**json.loads(cached))

    # ── Build query ───────────────────────────────────────────────────────────
    query = sa.select(Job).where(Job.is_active.is_(True))

    if q:
        pattern = f"%{q}%"
        query = query.where(Job.title.ilike(pattern) | Job.company.ilike(pattern))
    if city:
        query = query.where(Job.city.ilike(f"%{city}%"))
    if state:
        query = query.where(Job.state.ilike(state))
    if remote is not None:
        query = query.where(Job.remote.is_(remote))
    if seniority:
        query = query.where(Job.seniority == seniority)
    if contract_type:
        query = query.where(Job.contract_type == contract_type)
    if source:
        query = query.where(Job.source == source)
    if technologies:
        # ANY match: job.technologies && ARRAY[...] (PostgreSQL overlap operator)
        query = query.where(
            Job.technologies.op("&&")(pg_array(technologies, type_=sa.String))
        )
    if salary_min is not None:
        query = query.where(Job.salary_min >= salary_min)
    if salary_max is not None:
        query = query.where(Job.salary_max <= salary_max)

    # ── Count ─────────────────────────────────────────────────────────────────
    total: int = (
        await session.execute(
            sa.select(sa.func.count()).select_from(query.subquery())
        )
    ).scalar_one()

    # ── Sort ──────────────────────────────────────────────────────────────────
    order_clause = {
        SortOrder.recent: Job.created_at.desc(),
        SortOrder.salary_asc: sa.nullslast(Job.salary_min.asc()),
        SortOrder.salary_desc: sa.nullslast(Job.salary_min.desc()),
    }[sort]

    query = query.order_by(order_clause).offset((page - 1) * page_size).limit(page_size)
    jobs = (await session.execute(query)).scalars().all()

    response = JobListResponse(total=total, page=page, page_size=page_size, items=list(jobs))

    # ── Cache store ───────────────────────────────────────────────────────────
    await redis.setex(cache_key, settings.cache_ttl_seconds, response.model_dump_json())
    return response


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: uuid.UUID, session: SessionDep) -> Job:
    """Return full job detail including raw_description. 404 for unknown or inactive jobs."""
    job = (
        await session.execute(
            sa.select(Job).where(Job.id == job_id, Job.is_active.is_(True))
        )
    ).scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job
