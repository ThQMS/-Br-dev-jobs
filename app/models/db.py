import enum
import uuid
from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────────

class JobSource(str, enum.Enum):
    gupy = "gupy"
    linkedin = "linkedin"
    indeed = "indeed"
    remoteok = "remoteok"


class ContractType(str, enum.Enum):
    clt = "clt"
    pj = "pj"
    freelance = "freelance"
    internship = "internship"
    unknown = "unknown"


class Seniority(str, enum.Enum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    unknown = "unknown"


# ── Models ────────────────────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    source: Mapped[JobSource] = mapped_column(
        sa.Enum(JobSource, name="job_source"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    company: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(sa.String(128))
    state: Mapped[Optional[str]] = mapped_column(sa.String(64))
    remote: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    contract_type: Mapped[ContractType] = mapped_column(
        sa.Enum(ContractType, name="contract_type"), nullable=False, default=ContractType.unknown
    )
    seniority: Mapped[Seniority] = mapped_column(
        sa.Enum(Seniority, name="seniority"), nullable=False, default=Seniority.unknown
    )
    salary_min: Mapped[Optional[int]] = mapped_column(sa.Integer)
    salary_max: Mapped[Optional[int]] = mapped_column(sa.Integer)
    # Extracted by NLP pipeline; stored as plain text array
    technologies: Mapped[list[str]] = mapped_column(
        ARRAY(sa.String), nullable=False, server_default=sa.text("ARRAY[]::varchar[]")
    )
    raw_description: Mapped[Optional[str]] = mapped_column(sa.Text)
    url: Mapped[str] = mapped_column(sa.String(2048), unique=True, nullable=False)
    # sha256(title + company + city) — fast content-based dedup before NLP
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date: Mapped[date] = mapped_column(sa.Date, unique=True, nullable=False, index=True)
    total_jobs: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    new_jobs: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    expired_jobs: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # {"Python": 234, "React": 189, ...}
    top_technologies: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # {"São Paulo": 120, "Remoto": 98, ...}
    top_cities: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    avg_salary_junior: Mapped[Optional[int]] = mapped_column(sa.Integer)
    avg_salary_mid: Mapped[Optional[int]] = mapped_column(sa.Integer)
    avg_salary_senior: Mapped[Optional[int]] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


# ── Session dependency ────────────────────────────────────────────────────────

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
