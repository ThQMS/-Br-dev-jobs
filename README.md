# br-dev-jobs

![CI](https://github.com/ThQMS/-Br-dev-jobs/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
![Redis](https://img.shields.io/badge/Redis-cache-DC382D)
![spaCy](https://img.shields.io/badge/spaCy-NLP-09A3D5)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

> **ETL pipeline and analytics API for the Brazilian developer job market.**
> It collects job posts from Gupy, LinkedIn, Indeed, and RemoteOK; normalizes
> them into a single schema; deduplicates and enriches them with NLP-extracted
> technologies; stores everything in PostgreSQL; and serves searchable listings
> plus aggregate market insights through a FastAPI service with a built-in
> dashboard.

The project is structured as a portfolio-grade async backend: source adapters
isolate scraping logic, the ETL layer standardizes raw listings, a scheduler
runs the pipeline on an interval, and the API exposes both granular job search
and market-level analytics — all running from a single `docker compose up`.

---

## Table of Contents

- [Highlights](#highlights)
- [Architecture](#architecture)
  - [System Context](#system-context)
  - [Application Layers](#application-layers)
  - [Request Lifecycle](#request-lifecycle)
- [ETL Pipeline](#etl-pipeline)
- [Data Model](#data-model)
- [Job Lifecycle](#job-lifecycle)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Development](#local-development)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Data Processing Internals](#data-processing-internals)
- [Sources](#sources)
- [Dashboard](#dashboard)
- [Caching Strategy](#caching-strategy)
- [Testing & Quality](#testing--quality)
- [Docker Image](#docker-image)
- [Make Commands](#make-commands)
- [Production Notes](#production-notes)
- [Contributing](#contributing)
- [License](#license)

---

## Highlights

- **Async end-to-end** — FastAPI, SQLAlchemy 2 (async), `asyncpg`, and `httpx`,
  with scrapers running concurrently under a configurable semaphore.
- **Four job sources** behind a common `BaseScraper` adapter interface, two of
  them Playwright-driven (LinkedIn, Indeed) with captcha detection and rate
  limiting.
- **Deterministic normalization** — contract type, seniority, location, and
  salary are parsed from messy free text into a canonical schema.
- **Content-hash deduplication** that collapses repeated postings across runs
  without rewriting history.
- **NLP enrichment** — a catalogue of **87 technology patterns** extracts and
  ranks the stack mentioned in each posting.
- **Market analytics** — daily snapshots power technology trends, salary
  percentiles by seniority, and city rankings.
- **Redis caching** for read-heavy endpoints, with explicit invalidation after
  each pipeline run.
- **Operable** — structured JSON logging, a health endpoint covering DB / Redis
  / scraper freshness, and a multi-stage Docker image running as a non-root user.

---

## Architecture

### System Context

```mermaid
flowchart LR
    User["User / Browser"] -->|HTTP| API
    Client["API Client"] -->|HTTP /api/v1| API

    subgraph App["FastAPI application (single container)"]
        API["REST API + Static Dashboard"]
        Scheduler["APScheduler<br/>interval trigger"]
        Pipeline["ETL Pipeline"]
        NLP["spaCy Enrichment<br/>pt_core_news_sm"]
    end

    API --> Redis[("Redis<br/>cache")]
    API --> PG[("PostgreSQL<br/>jobs + snapshots")]

    Scheduler --> Pipeline
    Pipeline --> Sources
    Pipeline --> NLP
    Pipeline --> PG
    Pipeline -->|invalidate| Redis

    subgraph Sources["Job Sources"]
        Gupy["Gupy (API)"]
        LinkedIn["LinkedIn (Playwright)"]
        Indeed["Indeed (Playwright)"]
        RemoteOK["RemoteOK (RSS)"]
    end
```

### Application Layers

The codebase is organized by responsibility. Routes never talk to scrapers;
the scheduler orchestrates the ETL layer; everything converges on the shared
models.

```mermaid
flowchart TB
    subgraph api["app/api"]
        Routes["routes/<br/>health · jobs · insights"]
        Deps["deps.py<br/>Session / Redis DI"]
    end

    subgraph core["app/core"]
        Config["config.py"]
        Logging["logging.py"]
        Exc["exceptions.py"]
    end

    subgraph pipeline["Pipeline"]
        Sched["app/scheduler<br/>jobs.py"]
        Scrapers["app/scrapers<br/>base · gupy · linkedin · indeed · remoteok"]
        ETL["app/etl<br/>normalizer · deduplicator · enricher"]
    end

    Models["app/models<br/>db.py (SQLAlchemy) · schemas.py (Pydantic)"]
    Web["web/<br/>static dashboard"]

    Routes --> Deps --> Models
    Routes --> Web
    Sched --> Scrapers
    Sched --> ETL
    ETL --> Models
    Sched --> Models
    Routes -.-> core
    pipeline -.-> core
```

### Request Lifecycle

Every request flows through middlewares (request-ID + structured logging, gzip,
CORS) before hitting a route, which reads through the Redis cache when possible.

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as Middlewares<br/>(RequestID · GZip · CORS)
    participant R as Route handler
    participant Cache as Redis
    participant DB as PostgreSQL

    C->>MW: GET /api/v1/jobs?q=python
    MW->>R: dispatch (request_id bound to logs)
    R->>Cache: GET jobs:list:<hash>
    alt cache hit
        Cache-->>R: cached JSON
        R-->>C: 200 (from cache)
    else cache miss
        R->>DB: SELECT ... WHERE filters
        DB-->>R: rows
        R->>Cache: SETEX jobs:list:<hash> (TTL)
        R-->>C: 200 (fresh)
    end
```

For a deeper architectural treatment (runtime topology, reliability notes,
deployment hardening), see **[docs/architecture.md](docs/architecture.md)**.

---

## ETL Pipeline

The scheduler runs the full pipeline on startup and then every
`SCRAPE_INTERVAL_HOURS`. The manual entrypoint is `run_full_pipeline()` (see
[`make scrape`](#make-commands)).

```mermaid
sequenceDiagram
    participant Sched as APScheduler
    participant Scr as Source Scrapers
    participant Norm as Normalizer
    participant Dedup as Deduplicator
    participant Enr as Enricher (spaCy)
    participant DB as PostgreSQL
    participant Cache as Redis

    Sched->>Scr: run all sources concurrently (semaphore)
    Scr-->>Sched: RawJob[] (failures isolated per source)
    loop each RawJob
        Sched->>Norm: normalize()
        Norm-->>Sched: JobCreate + content_hash
        Sched->>Dedup: is_duplicate(content_hash)?
        alt duplicate
            Dedup->>DB: UPDATE last_seen_at
        else new
            Sched->>Enr: extract_technologies()
            Enr-->>Sched: ranked tech list
            Sched->>DB: INSERT job (is_active = true)
        end
    end
    Sched->>DB: soft-expire jobs unseen > 7 days
    Sched->>DB: UPSERT daily_snapshot
    Sched->>Cache: invalidate insights:*
```

**Stages**

1. **Extract** — all enabled sources scraped concurrently; a failing source
   returns `[]` instead of breaking the run.
2. **Normalize** — raw listings become a canonical `JobCreate` with consistent
   source, location, contract type, seniority, and salary fields.
3. **Deduplicate** — `content_hash` is checked intra-batch and against the DB.
4. **Enrich** — technology mentions are extracted and ranked by frequency.
5. **Load** — new jobs are inserted; known jobs get `last_seen_at` refreshed.
6. **Expire** — listings unseen beyond the stale window are soft-expired
   (`is_active = false`), preserving history.
7. **Snapshot** — a `DailySnapshot` row is upserted with the day's aggregates.
8. **Invalidate** — cached insight responses are cleared so the next read is fresh.

---

## Data Model

```mermaid
erDiagram
    JOBS {
        uuid id PK
        string external_id "indexed"
        enum source "gupy|linkedin|indeed|remoteok"
        string title
        string company
        string city "nullable"
        string state "nullable"
        boolean remote
        enum contract_type "clt|pj|freelance|internship|unknown"
        enum seniority "junior|mid|senior|lead|unknown"
        int salary_min "nullable"
        int salary_max "nullable"
        string_array technologies "PG ARRAY"
        text raw_description "nullable"
        string url UK "unique hard guard"
        string content_hash "indexed, sha256[:16]"
        boolean is_active
        timestamptz first_seen_at
        timestamptz last_seen_at
        timestamptz created_at
    }

    DAILY_SNAPSHOTS {
        uuid id PK
        date date UK "indexed"
        int total_jobs
        int new_jobs
        int expired_jobs
        jsonb top_technologies "{tech: count}"
        jsonb top_cities "{city: count}"
        int avg_salary_junior "nullable"
        int avg_salary_mid "nullable"
        int avg_salary_senior "nullable"
        timestamptz created_at
    }
```

| Field | Why it exists |
|---|---|
| `url` | Unique source URL — a hard uniqueness guard at the DB level. |
| `content_hash` | `sha256(title \| company \| city)[:16]` — fast content dedup before NLP. |
| `technologies` | PostgreSQL `ARRAY`, queried with the `&&` overlap operator for "ANY of" filters. |
| `first_seen_at` / `last_seen_at` | Distinguish first appearance from latest sighting. |
| `is_active` | Soft expiry — stale jobs are hidden, never deleted, keeping the dataset usable for trend analysis. |
| `daily_snapshots` | Precomputed daily aggregates that make trend queries cheap. |

---

## Job Lifecycle

A listing's state is derived from when it was last seen by a scrape, never by
hard deletion.

```mermaid
stateDiagram-v2
    [*] --> New: first scrape
    New --> Active: normalized, enriched, inserted
    Active --> Active: seen again → last_seen_at refreshed
    Active --> Stale: not seen > 7 days
    Stale --> Active: reappears in a later scrape
    Stale --> [*]: remains soft-expired (is_active = false)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI · Uvicorn · Starlette middleware |
| Validation | Pydantic v2 · pydantic-settings |
| Database | PostgreSQL 16 · SQLAlchemy 2 (async) · asyncpg · Alembic |
| Cache | Redis 7 |
| Scraping | httpx · Playwright (Chromium) · feedparser |
| NLP | spaCy · `pt_core_news_sm` |
| Scheduling | APScheduler |
| Logging | structlog (JSON in prod, console in debug) |
| Dashboard | Static HTML / CSS / vanilla JS, served by FastAPI |
| Tooling | Ruff · mypy (strict) · pytest · Docker (multi-stage) |

---

## Project Structure

```text
br-dev-jobs/
├── app/
│   ├── main.py                # FastAPI app: lifespan, middlewares, routers, static mount
│   ├── api/
│   │   ├── deps.py            # Session / Redis dependency injection
│   │   └── routes/            # health, jobs, insights
│   ├── core/                  # config (env), logging (structlog), exceptions
│   ├── etl/
│   │   ├── normalizer.py      # raw → canonical schema (contract/seniority/salary/location)
│   │   ├── deduplicator.py    # content_hash + DB existence checks
│   │   └── enricher.py        # spaCy loader + 87-pattern tech catalogue
│   ├── models/
│   │   ├── db.py              # SQLAlchemy models + enums + async session
│   │   └── schemas.py         # Pydantic request/response models
│   ├── scrapers/              # base + gupy, linkedin, indeed, remoteok adapters
│   └── scheduler/jobs.py      # pipeline orchestration + APScheduler wiring
├── web/                       # static dashboard (index.html, css, js)
├── alembic/                   # database migrations
├── docs/architecture.md       # deep-dive architecture document
├── tests/                     # pytest suite (fully mocked, no external services)
├── Dockerfile                 # multi-stage build (builder → runtime)
├── docker-compose.yml         # api + db + redis
├── Makefile                   # up / scrape / test / lint
└── pyproject.toml             # deps, ruff, mypy, pytest config
```

---

## Quick Start (Docker)

Everything (API, scheduler, Postgres, Redis) comes up with one command.

```bash
cp .env.example .env
docker compose up --build -d

# verify the API is healthy
curl http://localhost:8000/api/v1/health
```

Then open:

| URL | What |
|---|---|
| <http://localhost:8000> | Dashboard |
| <http://localhost:8000/api/docs> | Swagger UI |
| <http://localhost:8000/api/redoc> | ReDoc |
| <http://localhost:8000/api/openapi.json> | OpenAPI schema |

> On startup the app creates missing tables, loads (or downloads) the spaCy
> model, pings Redis, and triggers the first scrape immediately — so the
> dashboard has data within the first pipeline run.

Trigger a scrape manually at any time:

```bash
make scrape
```

---

## Local Development

Run the app directly while pointing at local Postgres and Redis (e.g. via
`docker compose up db redis -d`).

```bash
# 1. create a virtualenv (Python 3.12)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. install with dev extras
pip install -e ".[dev]"

# 3. install runtime browser + NLP model
python -m playwright install chromium
python -m spacy download pt_core_news_sm

# 4. run the API with autoreload
uvicorn app.main:app --reload
```

> **Note on local vs CI parity:** CI runs on **Python 3.12** with the latest
> `ruff`/`mypy`. If your local Python or tool versions differ, lint/type results
> can diverge. The CI quality gate is the source of truth — see
> [Testing & Quality](#testing--quality).

---

## Configuration

Configuration is loaded from environment variables / `.env` via
`pydantic-settings`. Copy `.env.example` and adjust as needed.

| Variable | Default | Purpose |
|---|---|---|
| `APP_NAME` | `br-dev-jobs` | Application name. |
| `DEBUG` | `false` | Debug mode; enables SQL echo and console log rendering. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/brdevjobs` | Async SQLAlchemy DSN. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL. |
| `SCRAPE_INTERVAL_HOURS` | `6` | Scheduler interval between pipeline runs. |
| `MAX_CONCURRENT_SCRAPERS` | `4` | Max scrapers running simultaneously. |
| `PLAYWRIGHT_HEADLESS` | `true` | Headless mode for browser-driven scrapers. |
| `GUPY_API_URL` | `https://portal.api.gupy.io/api/job` | Gupy source endpoint. |
| `REMOTEOK_RSS_URL` | `https://remoteok.com/remote-dev-jobs.rss` | RemoteOK feed. |
| `CACHE_TTL_SECONDS` | `300` | TTL for the job-listing cache. |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated allowed origins (`*` = all). |

---

## API Reference

All endpoints are versioned under `/api/v1`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness/readiness: DB, Redis, and per-source scraper freshness. |
| `GET` | `/api/v1/jobs` | Search & list jobs with filters, sorting, and pagination. |
| `GET` | `/api/v1/jobs/{job_id}` | Full job detail, including `raw_description`. |
| `GET` | `/api/v1/insights` | Dashboard aggregate: counts, top techs (with weekly trend), salaries, cities. |
| `GET` | `/api/v1/insights/technologies` | Technology stack ranking with share-of-postings. |
| `GET` | `/api/v1/insights/salaries` | Salary analytics (percentiles by seniority, by-tech benchmarks). |

### `GET /api/v1/jobs` — query parameters

| Param | Type | Notes |
|---|---|---|
| `q` | string | Full-text match on title **and** company. |
| `city` | string | Partial, case-insensitive match. |
| `state` | string | Exact (e.g. `SP`). |
| `remote` | bool | Remote-only / on-site-only filter. |
| `seniority` | enum | `junior` · `mid` · `senior` · `lead` · `unknown`. |
| `contract_type` | enum | `clt` · `pj` · `freelance` · `internship` · `unknown`. |
| `source` | enum | `gupy` · `linkedin` · `indeed` · `remoteok`. |
| `technologies` | string (repeatable) | **ANY** match — job must list at least one. |
| `salary_min` / `salary_max` | int ≥ 0 | Salary band filter (BRL). |
| `page` / `page_size` | int | `page ≥ 1`; `page_size` 1–100 (default 20). |
| `sort` | enum | `recent` (default) · `salary_asc` · `salary_desc`. |

### Examples

```bash
# Health
curl http://localhost:8000/api/v1/health

# Remote Python jobs, first page
curl "http://localhost:8000/api/v1/jobs?q=python&remote=true&page=1&page_size=10"

# Senior roles requiring Python OR FastAPI, highest salary first
curl "http://localhost:8000/api/v1/jobs?technologies=Python&technologies=FastAPI&seniority=senior&sort=salary_desc"

# A single job's full detail
curl http://localhost:8000/api/v1/jobs/<job_id>

# Market dashboard
curl http://localhost:8000/api/v1/insights

# Technology trends and salary analytics
curl http://localhost:8000/api/v1/insights/technologies
curl http://localhost:8000/api/v1/insights/salaries
```

---

## Data Processing Internals

How messy source data becomes clean, queryable records:

- **Contract type** — regex-matched from title/raw fields:
  `clt`, `pj` (incl. *pessoa jurídica*), `freelance`/*freela*,
  `internship`/*estágio*/*estagiário*; defaults to `unknown`.
- **Seniority** — matched against the **title only** (descriptions are too noisy):
  `junior`/`jr`, `pleno`/`mid`, `sênior`/`sr`, `lead`/*líder*/`staff`.
- **Salary parsing** — handles the formats Brazilian listings actually use:
  - k-notation ranges: `5k-8k`, `5,5k–8k`
  - upper bound: `até R$ 10.000`
  - BRL ranges & singles: `R$ 5.000 a R$ 8.000`, `R$ 6.000`
  - thousands separators are stripped and cents dropped (`8.000` → `8000`).
- **Location** — `"São Paulo, SP"` is split into city/state, with title-casing
  that respects Portuguese particles (*de*, *do*, *da*…) and uppercases state
  abbreviations.
- **Deduplication** — `content_hash = sha256(title | company | city)[:16]`
  catches reposts even when URLs differ; the unique `url` column is the final
  hard guard.
- **Enrichment** — a catalogue of **87 technology patterns** (languages,
  frameworks, databases, cloud, tooling) is matched case-insensitively and
  ranked by mention frequency per posting.

---

## Sources

| Source | Coverage | Method |
|---|---|---|
| **Gupy** | Brazilian company career pages / ATS | Public JSON API |
| **LinkedIn** | Public developer job listings | Playwright (Chromium) + captcha detection |
| **Indeed** | Public job-search results | Playwright (Chromium) + captcha detection |
| **RemoteOK** | Remote-friendly developer roles | RSS feed |

Each source implements the `BaseScraper` interface and returns `RawJob`
records, isolating source-specific quirks from the rest of the system. Runs are
wrapped with a per-scraper timeout and error isolation, so one flaky source
never aborts the pipeline.

---

## Dashboard

The static dashboard in `web/` is served directly by FastAPI at
`http://localhost:8000`. It consumes the same public `/api/v1` endpoints and is
the visual entry point for browsing listings and market insights. Unknown paths
fall back to `index.html` so client-side routing works.

---

## Caching Strategy

| Cache key family | Producer | Invalidation |
|---|---|---|
| `jobs:list:*` | `GET /api/v1/jobs` | TTL (`CACHE_TTL_SECONDS`, default 300s). |
| `insights:*` | `GET /api/v1/insights*` | Explicitly cleared when a pipeline run completes. |

List responses are cached under a hash of the full query parameter set, so
distinct filter combinations are cached independently. Insight caches favor
longer lifetimes and are flushed by the ETL run rather than by time.

---

## Testing & Quality

The CI quality gate (`.github/workflows/ci.yml`) runs on **Python 3.12** and
enforces four steps, all of which you can run locally:

```bash
python -m ruff format --check .   # formatting
python -m ruff check .            # lint
python -m mypy app                # strict type checking
python -m pytest                  # tests (with coverage)
```

A separate CI job builds the Docker image to catch packaging regressions.

The test suite is **fully mocked** — the FastAPI lifespan is replaced with a
no-op and the Session/Redis dependencies are overridden with async mocks, so
tests never touch Postgres, Redis, spaCy, or the network.

---

## Docker Image

Multi-stage build (`Dockerfile`):

- **`builder`** — installs production dependencies (derived from
  `pyproject.toml`) into an isolated prefix.
- **`runtime`** — `python:3.12-slim` with Chromium + Chromium Driver, the
  Playwright Chromium browser, and the `pt_core_news_sm` spaCy model. Runs as a
  non-root `appuser`.

Compose services:

| Service | Image / build | Role |
|---|---|---|
| `api` | local Dockerfile | API + dashboard + scheduler + pipeline (port `8000`). |
| `db` | `postgres:16-alpine` | Storage; named volume + healthcheck. |
| `redis` | `redis:7-alpine` | Cache; named volume + healthcheck. |

The `api` service waits for `db` and `redis` healthchecks before starting.

---

## Make Commands

```bash
make up       # docker compose up -d
make scrape   # run the full ETL pipeline once, inside the api container
make test     # run pytest with coverage in a throwaway container
make lint     # ruff format --check + ruff check + mypy
```

---

## Production Notes

This stack is dev-friendly out of the box; for production consider:

- Replacing startup `create_all` with an explicit **Alembic migration** step.
- Moving the **scheduler into a dedicated worker** if you scale the API
  horizontally (so the pipeline runs once, not per replica).
- Setting a restrictive **`CORS_ALLOW_ORIGINS`**.
- Adding **rate limiting** around public endpoints and scrape triggers.
- Wiring **observability** — metrics, traces, and log aggregation on top of the
  existing structured logs.

---

## Contributing

Contributions are welcome. Please read
[CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
and [SECURITY.md](SECURITY.md) before opening a PR.

## License

[MIT](LICENSE)
