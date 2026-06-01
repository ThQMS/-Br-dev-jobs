"""
Tests for GET /api/v1/jobs and GET /api/v1/jobs/{id}.

Uses AsyncMock session — no PostgreSQL required.  The mock is configured with
side_effect lists to simulate the two sequential execute() calls the list
endpoint makes (count query, then jobs query).
"""

import uuid
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from tests.conftest import make_job


def _mock_list(session_mock: object, jobs: list, total: int) -> None:
    """
    Wire mock_session.execute so the first await returns the count,
    the second returns the jobs list.
    """
    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    jobs_result = MagicMock()
    jobs_result.scalars.return_value.all.return_value = jobs

    session_mock.execute.side_effect = [count_result, jobs_result]  # type: ignore[attr-defined]


def _mock_single(session_mock: object, job: object | None) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    session_mock.execute.return_value = result  # type: ignore[attr-defined]


# ── List endpoint ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_jobs_returns_paginated(async_client: AsyncClient) -> None:
    jobs = [make_job(title=f"Dev {i}") for i in range(3)]
    _mock_list(async_client.mock_session, jobs, total=3)

    resp = await async_client.get("/api/v1/jobs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 1
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_list_jobs_pagination_math(async_client: AsyncClient) -> None:
    """total_pages is computed correctly from total and page_size."""
    _mock_list(async_client.mock_session, [], total=55)
    resp = await async_client.get("/api/v1/jobs?page_size=20")
    assert resp.json()["total_pages"] == 3


@pytest.mark.asyncio
async def test_list_jobs_empty_result(async_client: AsyncClient) -> None:
    _mock_list(async_client.mock_session, [], total=0)
    resp = await async_client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_filter_by_remote(async_client: AsyncClient) -> None:
    remote_job = make_job(remote=True, city=None)
    _mock_list(async_client.mock_session, [remote_job], total=1)

    resp = await async_client.get("/api/v1/jobs?remote=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["remote"] is True


@pytest.mark.asyncio
async def test_filter_by_seniority(async_client: AsyncClient) -> None:
    from app.models.db import Seniority

    senior_job = make_job(seniority=Seniority.senior)
    _mock_list(async_client.mock_session, [senior_job], total=1)

    resp = await async_client.get("/api/v1/jobs?seniority=senior")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["seniority"] == "senior"


@pytest.mark.asyncio
async def test_filter_by_technology(async_client: AsyncClient) -> None:
    python_job = make_job(technologies=["Python", "Django"])
    _mock_list(async_client.mock_session, [python_job], total=1)

    resp = await async_client.get("/api/v1/jobs?technologies=Python")

    assert resp.status_code == 200
    assert "Python" in resp.json()["items"][0]["technologies"]


@pytest.mark.asyncio
async def test_filter_by_source(async_client: AsyncClient) -> None:
    from app.models.db import JobSource

    gupy_job = make_job(source=JobSource.gupy)
    _mock_list(async_client.mock_session, [gupy_job], total=1)

    resp = await async_client.get("/api/v1/jobs?source=gupy")

    assert resp.status_code == 200
    assert resp.json()["items"][0]["source"] == "gupy"


@pytest.mark.asyncio
async def test_page_size_validation_max(async_client: AsyncClient) -> None:
    """page_size > 100 should be rejected with 422."""
    _mock_list(async_client.mock_session, [], total=0)
    resp = await async_client.get("/api/v1/jobs?page_size=101")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_salary_range_in_response(async_client: AsyncClient) -> None:
    """salary_range computed_field is present and formatted."""
    job = make_job(salary_min=8_000, salary_max=12_000)
    _mock_list(async_client.mock_session, [job], total=1)

    resp = await async_client.get("/api/v1/jobs")
    item = resp.json()["items"][0]
    assert item["salary_range"] == "R$ 8k–12k"


# ── Detail endpoint ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_job_by_id(async_client: AsyncClient) -> None:
    job = make_job()
    _mock_single(async_client.mock_session, job)

    resp = await async_client.get(f"/api/v1/jobs/{job.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(job.id)
    assert data["title"] == job.title
    assert data["company"] == job.company


@pytest.mark.asyncio
async def test_get_job_returns_raw_description(async_client: AsyncClient) -> None:
    job = make_job(raw_description="Experiência com Python e AWS.")
    _mock_single(async_client.mock_session, job)

    resp = await async_client.get(f"/api/v1/jobs/{job.id}")

    assert resp.status_code == 200
    assert resp.json()["raw_description"] == "Experiência com Python e AWS."


@pytest.mark.asyncio
async def test_get_nonexistent_job_returns_404(async_client: AsyncClient) -> None:
    _mock_single(async_client.mock_session, None)

    resp = await async_client.get(f"/api/v1/jobs/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_job_invalid_uuid_returns_422(async_client: AsyncClient) -> None:
    """Non-UUID path param must be rejected before hitting the DB."""
    resp = await async_client.get("/api/v1/jobs/not-a-uuid")
    assert resp.status_code == 422
    async_client.mock_session.execute.assert_not_called()
