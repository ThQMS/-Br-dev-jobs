"""
Shared fixtures for br-dev-jobs tests.

Design choices:
- The FastAPI lifespan is replaced with a no-op, so tests never connect to
  PostgreSQL, Redis, or trigger the spaCy download / APScheduler startup.
- async_client overrides the session and Redis dependencies with AsyncMocks,
  allowing callers to configure mock return values per test.
- make_job() creates SimpleNamespace objects; Pydantic's from_attributes=True
  reads them correctly, including the salary_range computed_field.
- dedup tests use pure-Python AsyncMocks — no SQLite/aiosqlite dependency.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as _main_module
from app.api.deps import get_redis, get_session
from app.models.db import ContractType, JobSource, Seniority
from app.scrapers.base import RawJob


# ── Lifespan no-op ────────────────────────────────────────────────────────────

@asynccontextmanager
async def _noop_lifespan(app: object) -> AsyncGenerator[None, None]:
    yield


# ── Sample raw jobs ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_raw_jobs() -> list[RawJob]:
    """10 realistic RawJob objects covering all 4 sources and varied attributes."""
    return [
        RawJob(
            source="gupy", external_id="g1",
            title="Desenvolvedor Python Sênior", company="TechCorp",
            city="São Paulo", state="SP", url="https://gupy.io/jobs/1",
            salary_raw="R$ 12.000 - R$ 18.000", contract_type_raw="clt",
            description="Python, Django, PostgreSQL, Docker",
        ),
        RawJob(
            source="gupy", external_id="g2",
            title="Engenheiro de Software Pleno", company="Startup XYZ",
            city="São Paulo", state="SP", url="https://gupy.io/jobs/2",
            salary_raw="R$ 8.000 a R$ 12.000", contract_type_raw="pj",
            description="React, TypeScript, Node.js",
        ),
        RawJob(
            source="linkedin", external_id="l1",
            title="Dev Python Junior", company="Big Bank SA",
            city="Rio de Janeiro, RJ", url="https://linkedin.com/jobs/1",
            salary_raw="R$ 4.000 - R$ 6.000",
            description="Python, SQL, Git",
        ),
        RawJob(
            source="linkedin", external_id="l2",
            title="Tech Lead Backend", company="Fintech Brasil",
            city="Remoto", remote=True, url="https://linkedin.com/jobs/2",
            salary_raw="8k-15k",
            description="Golang, Kubernetes, AWS",
        ),
        RawJob(
            source="indeed", external_id="i1",
            title="Desenvolvedor Full Stack Sênior", company="E-commerce SA",
            city="Curitiba, PR", url="https://indeed.com/jobs/1",
            salary_raw="R$ 10.000 - R$ 14.000", contract_type_raw="clt",
            description="React, Django, Redis, Docker",
        ),
        RawJob(
            source="indeed", external_id="i2",
            title="Estagiário de Desenvolvimento", company="Agência Digital",
            city="Belo Horizonte, MG", url="https://indeed.com/jobs/2",
            salary_raw="R$ 1.500", contract_type_raw="internship",
            description="HTML, CSS, JavaScript",
        ),
        RawJob(
            source="remoteok", external_id="r1",
            title="Senior Python Engineer", company="Remote First Co",
            remote=True, url="https://remoteok.com/1",
            salary_raw="até R$ 25.000",
            description="Python, FastAPI, PostgreSQL, AWS",
        ),
        RawJob(
            source="remoteok", external_id="r2",
            title="Frontend Developer React", company="Global Startup",
            remote=True, url="https://remoteok.com/2",
            salary_raw="5k-9k",
            description="React, TypeScript, GraphQL",
        ),
        RawJob(
            source="gupy", external_id="g3",
            title="DevOps Engineer", company="Cloud Company",
            city="Porto Alegre, RS", url="https://gupy.io/jobs/3",
            description="Docker, Kubernetes, Terraform, AWS, GCP",
        ),
        RawJob(
            source="gupy", external_id="g4",
            title="Analista de Dados", company="Data Insights",
            city="São Paulo", state="SP", url="https://gupy.io/jobs/4",
            description="Python, Pandas, SQL, Spark",
        ),
    ]


# ── Playwright mock ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_playwright(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Prevent real browser launches in scraper tests."""
    mock_page = AsyncMock()
    mock_page.query_selector_all.return_value = []
    mock_page.wait_for_selector = AsyncMock()
    mock_page.goto = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.new_page.return_value = mock_page

    mock_pw = AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    cm = AsyncMock()
    cm.__aenter__.return_value = mock_pw
    cm.__aexit__.return_value = False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: cm)
    return mock_page


# ── Job factory ───────────────────────────────────────────────────────────────

def make_job(**overrides: object) -> SimpleNamespace:
    """
    Create a SimpleNamespace that looks like a Job ORM object to Pydantic.
    Pydantic's from_attributes=True reads attributes via getattr(), which
    works correctly on SimpleNamespace.
    """
    now = datetime.now(tz=timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        external_id=str(uuid.uuid4())[:8],
        source=JobSource.gupy,
        title="Dev Python Sênior",
        company="TechCorp",
        city="São Paulo",
        state="SP",
        remote=False,
        seniority=Seniority.senior,
        contract_type=ContractType.clt,
        salary_min=12_000,
        salary_max=18_000,
        technologies=["Python", "Django", "PostgreSQL"],
        url=f"https://example.com/jobs/{uuid.uuid4()}",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        raw_description="Python developer with Django experience.",
        content_hash=uuid.uuid4().hex[:16],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── async_client ──────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    AsyncClient with:
    - No-op lifespan (no PostgreSQL / Redis / spaCy / APScheduler).
    - AsyncMock session exposed as client.mock_session.
    - AsyncMock redis  exposed as client.mock_redis.

    Configure side_effect / return_value on mock_session.execute
    before each test call.
    """
    mock_session = AsyncMock(spec=AsyncSession)
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None   # cache miss by default
    mock_redis.setex.return_value = True
    mock_redis.keys.return_value = []
    mock_redis.ping.return_value = True

    async def _session_dep() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    async def _redis_dep() -> AsyncGenerator[object, None]:
        yield mock_redis

    original_lifespan = _main_module.app.router.lifespan_context
    _main_module.app.router.lifespan_context = _noop_lifespan
    _main_module.app.dependency_overrides[get_session] = _session_dep
    _main_module.app.dependency_overrides[get_redis] = _redis_dep

    async with AsyncClient(
        transport=ASGITransport(app=_main_module.app),
        base_url="http://test",
    ) as client:
        client.mock_session = mock_session   # type: ignore[attr-defined]
        client.mock_redis = mock_redis       # type: ignore[attr-defined]
        yield client

    _main_module.app.router.lifespan_context = original_lifespan
    _main_module.app.dependency_overrides.clear()
