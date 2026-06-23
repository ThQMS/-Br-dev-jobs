"""
Tests for app.etl.deduplicator.

is_duplicate and filter_new are tested with AsyncMock sessions — no database
connection required. compute_hash is a pure function.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.etl.deduplicator import compute_hash, filter_new, is_duplicate
from app.models.db import ContractType, JobSource, Seniority
from app.models.schemas import JobCreate

# ── Helpers ───────────────────────────────────────────────────────────────────


def _job_create(
    *,
    title: str = "Dev Python",
    company: str = "ACME",
    city: str | None = "São Paulo",
    content_hash: str | None = None,
) -> JobCreate:
    h = content_hash or compute_hash(title, company, city)
    return JobCreate(
        external_id="ext-1",
        source=JobSource.gupy,
        title=title,
        company=company,
        city=city,
        remote=False,
        contract_type=ContractType.unknown,
        seniority=Seniority.unknown,
        url=f"https://example.com/{h}",
        content_hash=h,
    )


def _session_returning(exists_value: bool) -> AsyncMock:
    """Build an AsyncMock session whose execute() simulates EXISTS query result."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = exists_value
    session.execute.return_value = result
    return session


# ── compute_hash ──────────────────────────────────────────────────────────────


def test_hash_deterministic() -> None:
    h1 = compute_hash("Dev Python", "ACME", "São Paulo")
    h2 = compute_hash("Dev Python", "ACME", "São Paulo")
    assert h1 == h2


def test_hash_is_16_chars() -> None:
    assert len(compute_hash("Dev Python", "ACME", "São Paulo")) == 16


def test_hash_is_hex() -> None:
    h = compute_hash("Dev Python", "ACME", "São Paulo")
    int(h, 16)  # raises ValueError if not valid hex


def test_hash_case_insensitive() -> None:
    assert compute_hash("DEV PYTHON", "acme", "SÃO PAULO") == compute_hash(
        "dev python", "ACME", "são paulo"
    )


def test_hash_none_city() -> None:
    h = compute_hash("Dev", "Corp", None)
    assert len(h) == 16


def test_different_city_not_duplicate() -> None:
    """Same title/company but different cities produce different hashes."""
    h_sp = compute_hash("Dev Python", "ACME", "São Paulo")
    h_rj = compute_hash("Dev Python", "ACME", "Rio de Janeiro")
    assert h_sp != h_rj


def test_different_company_not_duplicate() -> None:
    h1 = compute_hash("Dev Python", "Company A", "São Paulo")
    h2 = compute_hash("Dev Python", "Company B", "São Paulo")
    assert h1 != h2


# ── is_duplicate ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_job_is_duplicate() -> None:
    """is_duplicate returns True when EXISTS query returns True."""
    session = _session_returning(exists_value=True)
    assert await is_duplicate(session, "abc123def456789a") is True
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_new_job_is_not_duplicate() -> None:
    session = _session_returning(exists_value=False)
    assert await is_duplicate(session, "abc123def456789a") is False


@pytest.mark.asyncio
async def test_is_duplicate_calls_execute_once() -> None:
    """is_duplicate must use a single query (SELECT EXISTS) per call."""
    session = _session_returning(exists_value=False)
    await is_duplicate(session, "somehash1234567a")
    assert session.execute.call_count == 1


# ── filter_new ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_new_removes_intra_batch_duplicates() -> None:
    """filter_new deduplicates within the batch before hitting the DB."""
    session = _session_returning(exists_value=False)
    job = _job_create()
    # Two identical jobs in the same batch
    result = await filter_new(session, [job, job])
    assert len(result) == 1
    # DB queried only once (second is eliminated in-memory)
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_filter_new_removes_db_duplicates() -> None:
    """filter_new drops jobs already in the DB."""
    session = _session_returning(exists_value=True)
    jobs = [_job_create(title="A"), _job_create(title="B")]
    result = await filter_new(session, jobs)
    assert result == []


@pytest.mark.asyncio
async def test_filter_new_keeps_genuinely_new_jobs() -> None:
    session = _session_returning(exists_value=False)
    jobs = [_job_create(title="A"), _job_create(title="B")]
    result = await filter_new(session, jobs)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_filter_new_empty_list() -> None:
    session = _session_returning(exists_value=False)
    assert await filter_new(session, []) == []
    session.execute.assert_not_called()
