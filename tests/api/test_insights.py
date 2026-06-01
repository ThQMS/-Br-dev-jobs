"""
Tests for GET /api/v1/insights (dashboard), /insights/technologies, /insights/salaries.

Cache-hit path is tested by having mock_redis.get() return pre-built JSON.
The cache-miss path (full DB aggregation) requires extensive session mocking;
those tests are limited to verifying HTTP 200 from the cache layer, which is
the behaviour most critical to protect in CI.
"""

import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


# ── Shared test data ──────────────────────────────────────────────────────────

_DASHBOARD_JSON = {
    "total_active_jobs": 250,
    "new_today": 12,
    "new_this_week": 47,
    "top_technologies": [
        {"technology": "Python", "count": 120, "percentage": 48.0, "trend": 8.5},
        {"technology": "React",  "count": 90,  "percentage": 36.0, "trend": -2.1},
    ],
    "salary_by_seniority": [
        {"seniority": "junior", "median": 5000,  "p25": 4000,  "p75": 6500,  "sample_size": 30},
        {"seniority": "senior", "median": 15000, "p25": 12000, "p75": 19000, "sample_size": 25},
    ],
    "top_cities": [
        {"city": "São Paulo", "state": "SP", "count": 80, "remote_percentage": 35.0},
        {"city": "Remoto",    "state": "",   "count": 60, "remote_percentage": 100.0},
    ],
    "daily_volume": [
        {"date": "2026-05-30", "total": 245, "new": 8},
        {"date": "2026-05-31", "total": 248, "new": 10},
        {"date": "2026-06-01", "total": 250, "new": 12},
    ],
    "last_updated": "2026-06-01T12:00:00+00:00",
}

_TECH_JSON = [
    {"technology": "Python",     "count": 120, "percentage": 48.0, "trend": 8.5},
    {"technology": "JavaScript", "count": 85,  "percentage": 34.0, "trend": 1.2},
]

_SALARY_JSON = {
    "by_seniority": [
        {"seniority": "junior", "median": 5000,  "p25": 4000,  "p75": 6500,  "sample_size": 30},
        {"seniority": "senior", "median": 15000, "p25": 12000, "p75": 19000, "sample_size": 25},
    ],
    "by_technology": [
        {"technology": "Python", "median": 14000, "p25": 11000, "p75": 17000, "sample_size": 18},
    ],
}


# ── GET /insights (dashboard) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insights_dashboard_structure(async_client: AsyncClient) -> None:
    """Dashboard returns all required top-level fields when served from cache."""
    async_client.mock_redis.get.return_value = json.dumps(_DASHBOARD_JSON)

    resp = await async_client.get("/api/v1/insights")

    assert resp.status_code == 200
    data = resp.json()

    # Required top-level fields
    for key in (
        "total_active_jobs", "new_today", "new_this_week",
        "top_technologies", "salary_by_seniority", "top_cities",
        "daily_volume", "last_updated",
    ):
        assert key in data, f"missing key: {key}"

    # Value types
    assert isinstance(data["total_active_jobs"], int)
    assert isinstance(data["top_technologies"], list)
    assert isinstance(data["salary_by_seniority"], list)
    assert isinstance(data["top_cities"], list)
    assert isinstance(data["daily_volume"], list)


@pytest.mark.asyncio
async def test_insights_top_technology_fields(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_DASHBOARD_JSON)
    data = (await async_client.get("/api/v1/insights")).json()

    tech = data["top_technologies"][0]
    assert "technology" in tech
    assert "count" in tech
    assert "percentage" in tech
    assert "trend" in tech


@pytest.mark.asyncio
async def test_insights_salary_seniority_fields(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_DASHBOARD_JSON)
    data = (await async_client.get("/api/v1/insights")).json()

    sal = data["salary_by_seniority"][0]
    assert "seniority" in sal
    assert "median" in sal
    assert "p25" in sal
    assert "p75" in sal
    assert "sample_size" in sal


@pytest.mark.asyncio
async def test_insights_city_fields(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_DASHBOARD_JSON)
    data = (await async_client.get("/api/v1/insights")).json()

    city = data["top_cities"][0]
    assert "city" in city
    assert "count" in city
    assert "remote_percentage" in city


@pytest.mark.asyncio
async def test_insights_cached_on_second_call(async_client: AsyncClient) -> None:
    """Both calls hit the cache; the DB session is never touched."""
    cached = json.dumps(_DASHBOARD_JSON)
    async_client.mock_redis.get.return_value = cached

    resp1 = await async_client.get("/api/v1/insights")
    resp2 = await async_client.get("/api/v1/insights")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Session never touched — both responses served from Redis
    async_client.mock_session.execute.assert_not_called()
    # Redis.get called once per request
    assert async_client.mock_redis.get.call_count == 2


@pytest.mark.asyncio
async def test_insights_cache_miss_calls_redis_setex(async_client: AsyncClient) -> None:
    """On cache miss the route must store the result in Redis with setex."""
    from unittest.mock import MagicMock, AsyncMock

    async_client.mock_redis.get.return_value = None  # cache miss

    # Provide minimal valid returns for all DB queries in the insights route
    scalar_100  = MagicMock(scalar_one=MagicMock(return_value=100))
    scalar_12   = MagicMock(scalar_one=MagicMock(return_value=12))
    scalar_47   = MagicMock(scalar_one=MagicMock(return_value=47))
    empty_rows  = MagicMock(fetchall=MagicMock(return_value=[]))

    async_client.mock_session.execute.side_effect = [
        scalar_100,  # total_active
        scalar_12,   # new_today
        scalar_47,   # new_this_week
        empty_rows,  # top_tech (all_tech text query)
        empty_rows,  # prev_week text query
        empty_rows,  # curr_week text query
        empty_rows,  # salary_by_seniority text query
        empty_rows,  # top_cities text query
        MagicMock(fetchall=MagicMock(return_value=[])),  # DailySnapshot ORM query
    ]

    resp = await async_client.get("/api/v1/insights")

    assert resp.status_code == 200
    async_client.mock_redis.setex.assert_called_once()


# ── GET /insights/technologies ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_technologies_endpoint_structure(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_TECH_JSON)

    resp = await async_client.get("/api/v1/insights/technologies")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2

    item = data[0]
    assert "technology" in item
    assert "count" in item
    assert "percentage" in item
    assert "trend" in item


@pytest.mark.asyncio
async def test_technologies_cached(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_TECH_JSON)

    await async_client.get("/api/v1/insights/technologies")
    await async_client.get("/api/v1/insights/technologies")

    async_client.mock_session.execute.assert_not_called()


# ── GET /insights/salaries ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_salaries_endpoint_structure(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_SALARY_JSON)

    resp = await async_client.get("/api/v1/insights/salaries")

    assert resp.status_code == 200
    data = resp.json()
    assert "by_seniority" in data
    assert "by_technology" in data
    assert isinstance(data["by_seniority"], list)
    assert isinstance(data["by_technology"], list)


@pytest.mark.asyncio
async def test_salaries_by_seniority_fields(async_client: AsyncClient) -> None:
    async_client.mock_redis.get.return_value = json.dumps(_SALARY_JSON)
    data = (await async_client.get("/api/v1/insights/salaries")).json()

    entry = data["by_seniority"][0]
    for field in ("seniority", "median", "p25", "p75", "sample_size"):
        assert field in entry
