import hashlib

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Job
from app.models.schemas import JobCreate


def compute_hash(title: str, company: str, city: str | None) -> str:
    """Stable 16-char fingerprint of (title, company, city) — used for content-based dedup.

    16 hex chars = 64-bit space; collision probability is negligible for job-scale data sets.
    """
    payload = f"{title.lower().strip()}|{company.lower().strip()}|{(city or '').lower().strip()}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def is_duplicate(session: AsyncSession, content_hash: str) -> bool:
    """Return True when a record with this content_hash already exists."""
    stmt = select(exists().where(Job.content_hash == content_hash))
    return bool((await session.execute(stmt)).scalar())


async def filter_new(session: AsyncSession, jobs: list[JobCreate]) -> list[JobCreate]:
    """Remove intra-batch duplicates and jobs already present in the DB."""
    unique: list[JobCreate] = []
    seen: set[str] = set()

    for job in jobs:
        h = job.content_hash
        if h in seen:
            continue
        seen.add(h)
        if not await is_duplicate(session, h):
            unique.append(job)

    return unique
