import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.db import ContractType, JobSource, Seniority

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fmt_k(value: int) -> str:
    """Format an integer salary as a compact BRL string. 8000 → '8k', 4500 → '4,5k'."""
    k = value / 1000
    if k == int(k):
        return f"{int(k)}k"
    return f"{k:.1f}k".replace(".", ",")


# ── Internal (ETL write) ──────────────────────────────────────────────────────


class JobCreate(BaseModel):
    """Normalised job produced by the ETL pipeline, ready to persist."""

    external_id: str
    source: JobSource
    title: str
    company: str
    city: str | None = None
    state: str | None = None
    remote: bool = False
    contract_type: ContractType = ContractType.unknown
    seniority: Seniority = Seniority.unknown
    salary_min: int | None = None
    salary_max: int | None = None
    technologies: list[str] = []
    raw_description: str | None = None
    url: str
    content_hash: str


# ── Job read schemas ──────────────────────────────────────────────────────────


class JobResponse(BaseModel):
    """Public representation of a job listing returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    source: JobSource
    title: str
    company: str
    city: str | None = None
    state: str | None = None
    remote: bool
    contract_type: ContractType
    seniority: Seniority
    salary_min: int | None = None
    salary_max: int | None = None
    technologies: list[str] = []
    url: str
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def salary_range(self) -> str:
        """Human-readable salary band.  '' when no salary data is available."""
        if self.salary_min is None:
            return ""
        lo = _fmt_k(self.salary_min)
        if self.salary_max is not None:
            return f"R$ {lo}–{_fmt_k(self.salary_max)}"
        return f"R$ {lo}+"


# Backward-compatible alias kept for existing route imports
JobRead = JobResponse


class JobDetailResponse(JobResponse):
    """Full job detail including raw description, returned by GET /jobs/{id}."""

    raw_description: str | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    page: int
    page_size: int

    @computed_field  # type: ignore[misc]
    @property
    def total_pages(self) -> int:
        if self.page_size == 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


# ── Insights — detail schemas ─────────────────────────────────────────────────


class InsightsTechStack(BaseModel):
    technology: str
    count: int
    percentage: float
    trend: float  # percentage-point change vs the same metric one week ago


class InsightsSalary(BaseModel):
    seniority: str
    median: int
    p25: int
    p75: int
    sample_size: int


class InsightsCity(BaseModel):
    city: str
    state: str
    count: int
    remote_percentage: float


class InsightsDashboard(BaseModel):
    total_active_jobs: int
    new_today: int
    new_this_week: int
    top_technologies: list[InsightsTechStack]
    salary_by_seniority: list[InsightsSalary]
    top_cities: list[InsightsCity]
    # Each entry: {"date": "2026-06-01", "count": 42}
    daily_volume: list[dict[str, Any]]
    last_updated: datetime


# ── Insights — legacy summary (used by /api/v1/insights) ─────────────────────


class SalarySummary(BaseModel):
    avg_junior: int | None = None
    avg_mid: int | None = None
    avg_senior: int | None = None


class InsightResponse(BaseModel):
    total_jobs: int
    remote_percentage: float
    jobs_by_source: dict[str, int]
    jobs_by_seniority: dict[str, int]
    top_technologies: dict[str, int]
    top_cities: dict[str, int]
    salary: SalarySummary


# ── Salary insights ──────────────────────────────────────────────────────────


class SalaryByTech(BaseModel):
    technology: str
    median: int
    p25: int
    p75: int
    sample_size: int


class SalariesResponse(BaseModel):
    by_seniority: list[InsightsSalary]
    by_technology: list[SalaryByTech]


# ── Health ────────────────────────────────────────────────────────────────────


class CheckResult(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded"
    checks: dict[str, CheckResult]
    scrapers: list["ScraperStatus"]


# ── Scraper monitoring ────────────────────────────────────────────────────────


class ScraperRunStatus(str, enum.Enum):
    idle = "idle"
    running = "running"
    success = "success"
    error = "error"


class ScraperStatus(BaseModel):
    source: str
    last_run: datetime | None = None
    jobs_collected: int
    status: ScraperRunStatus
    error_message: str | None = None
