import asyncio
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
import spacy
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import health, insights, jobs
from app.core.config import settings
from app.core.exceptions import AppBaseError
from app.core.logging import configure_logging, get_logger
from app.models.db import Base, engine
from app.scheduler.jobs import create_scheduler

logger = get_logger(__name__)

_WEB_DIR = Path(__file__).parent.parent / "web"
_STATIC_DIR = _WEB_DIR / "static"
_INDEX_HTML = _WEB_DIR / "index.html"

# ── Request logging middleware ────────────────────────────────────────────────


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attaches a short request ID to every request, logs method/path/status/duration."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())[:8]

        # Bind per-request context so all log lines in this coroutine carry request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        t0 = time.monotonic()
        # call_next already wraps route errors via FastAPI exception handlers,
        # so we always receive a Response here — never a raw exception
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        logger.info(
            "http_request",
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            client=request.client.host if request.client else "unknown",
        )
        response.headers["X-Request-ID"] = request_id
        return response


# ── Startup helpers ───────────────────────────────────────────────────────────


async def _ensure_spacy_model(model: str) -> None:
    """Load the spaCy model; download it automatically if missing."""
    try:
        spacy.load(model)
        logger.info("spacy_model_ready", model=model)
    except OSError:
        logger.info("spacy_model_downloading", model=model)
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "spacy", "download", model],
            check=True,
        )
        spacy.load(model)  # verify download succeeded
        logger.info("spacy_model_downloaded", model=model)


async def _check_redis() -> None:
    """Ping Redis at startup so a misconfigured URL fails loudly, not silently."""
    client: aioredis.Redis = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
        logger.info("redis_connected", url=settings.redis_url)
    except Exception as exc:
        logger.warning("redis_unavailable", error=str(exc))
    finally:
        await client.aclose()


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ───────────────────────────────────────────────────────────────
    configure_logging()
    logger.info("app_starting", version=app.version, debug=settings.debug)

    # Database — create missing tables (dev); production uses alembic upgrade head
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_tables_ready")

    # spaCy model — download once, load cached thereafter
    await _ensure_spacy_model("pt_core_news_sm")

    # Redis — warm connection pool and verify connectivity
    await _check_redis()

    # APScheduler — next_run_time=now triggers one immediate run on startup
    scheduler = create_scheduler()
    scheduler.start()
    logger.info(
        "scheduler_started",
        interval_hours=settings.scrape_interval_hours,
        jobs=[j.id for j in scheduler.get_jobs()],
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    # wait=False: don't block shutdown if a pipeline job is currently running
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")

    await engine.dispose()
    logger.info("app_stopped")


# ── Application factory ───────────────────────────────────────────────────────

app = FastAPI(
    title="Brazilian Dev Jobs API",
    version="0.1.0",
    description="""
Aggregator and analytics engine for the Brazilian developer job market.

Scrapes from **Gupy**, **LinkedIn**, **Indeed** and **RemoteOK** every
`SCRAPE_INTERVAL_HOURS` hours (default 6 h), normalises and deduplicates
listings, extracts technology mentions with spaCy NLP, and exposes a REST API
with rich filtering and market-insight analytics.

## Key features

- **Job listings** — full-text search, 9 query filters, 3 sort modes, Redis cache
- **Insights dashboard** — top technologies with weekly trend, salary distribution
  by seniority (P25/P50/P75), city ranking, 30-day volume chart
- **Scraper health** — per-source last-run status inferred from DB data

## Stack

FastAPI · PostgreSQL (asyncpg) · Redis · APScheduler · spaCy · Playwright
""",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── Middlewares (added innermost-first; CORS must be outermost) ───────────────

app.add_middleware(RequestIDMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# ── Exception handlers ────────────────────────────────────────────────────────


@app.exception_handler(AppBaseError)
async def handle_app_error(request: Request, exc: AppBaseError) -> JSONResponse:
    logger.warning("app_error", error=exc.error, detail=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "detail": str(exc)},
    )


@app.exception_handler(Exception)
async def handle_generic_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        error=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
        exc_info=True,
    )
    # Leak internal details only in debug mode
    detail = str(exc) if settings.debug else "An unexpected error occurred."
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": detail},
    )


# ── API routers ───────────────────────────────────────────────────────────────

app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")

# ── Static files & dashboard ──────────────────────────────────────────────────

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(str(_INDEX_HTML))


@app.get("/{path:path}", include_in_schema=False)
async def spa_fallback(path: str) -> FileResponse:
    """Serve index.html for any unknown path so client-side routing works."""
    return FileResponse(str(_INDEX_HTML))
