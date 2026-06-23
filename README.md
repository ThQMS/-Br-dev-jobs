# br-dev-jobs

![CI](https://github.com/ThQMS/-Br-dev-jobs/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688)
![spaCy](https://img.shields.io/badge/spaCy-NLP-09A3D5)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

Pipeline ETL that collects, normalizes, and analyzes developer jobs in Brazil.

The project ships a FastAPI service, PostgreSQL storage, Redis caching, scheduled scrapers, spaCy-based enrichment, and a static dashboard served by the API.

It is designed as a portfolio-grade backend project for job-market analytics: source adapters isolate scraping logic, the ETL layer standardizes job data, and the API exposes searchable listings plus aggregate market insights.

## Quick Start

```bash
cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/api/v1/health
```

Open `http://localhost:8000` for the dashboard and `http://localhost:8000/api/docs` for the interactive API documentation.

## Documentation

- [Architecture](docs/architecture.md)

## Dashboard

The static dashboard is served from `web/` by FastAPI at `http://localhost:8000`. It consumes the same public API endpoints documented below and is intended as the visual entry point for browsing job-market insights.

## Generated Insights

Examples of analysis produced from normalized job data:

- Top technologies in active job posts, with weekly trend deltas.
- Salary distribution by seniority using P25, median, and P75.
- Top hiring cities and their remote-work percentage.
- New jobs today, new jobs this week, and 30-day posting volume.
- Salary benchmarks by technology when enough listings include salary data.

## How It Works

1. Extract: scheduled scrapers collect listings from Gupy, LinkedIn, Indeed, and RemoteOK.
2. Normalize: raw listings are converted into a shared job schema with consistent source, location, contract, salary, and seniority fields.
3. Deduplicate: repeated listings are matched and collapsed before persistence.
4. Enrich: spaCy and rule-based parsing extract technologies, seniority signals, remote status, and salary ranges.
5. Load: normalized jobs and daily snapshots are stored in PostgreSQL.
6. Serve: FastAPI exposes job search, market insights, and health endpoints, with Redis caching for repeated reads.

## API Reference

Health check:

```bash
curl http://localhost:8000/api/v1/health
```

List jobs with filters:

```bash
curl "http://localhost:8000/api/v1/jobs?q=python&remote=true&page=1&page_size=10"
```

Filter by technology and seniority:

```bash
curl "http://localhost:8000/api/v1/jobs?technologies=Python&technologies=FastAPI&seniority=senior"
```

Get dashboard insights:

```bash
curl http://localhost:8000/api/v1/insights
```

Get technology trends:

```bash
curl http://localhost:8000/api/v1/insights/technologies
```

Get salary analytics:

```bash
curl http://localhost:8000/api/v1/insights/salaries
```

## Sources

| Source | Coverage | Collection method |
|---|---|---|
| Gupy | Brazilian company career pages and ATS listings | Scraper |
| LinkedIn | Public developer job listings | Playwright-assisted scraper |
| Indeed | Public job search results | Scraper |
| RemoteOK | Remote-friendly developer roles | API/feed-style scraper |

## Docker

The application image uses a multi-stage Dockerfile:

- `builder`: installs production Python dependencies from `pyproject.toml`.
- `runtime`: installs Chromium, Chromium Driver, Playwright Chromium, the Portuguese spaCy model, and runs as `appuser`.

Services:

- `api`: FastAPI app on port `8000`, using `.env`.
- `db`: PostgreSQL 16 Alpine with a named volume and healthcheck.
- `redis`: Redis 7 Alpine with a named volume and healthcheck.

## Make Commands

```bash
make up
make scrape
make test
make lint
```

## Tests & Quality

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy app
python -m pytest
```

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI, Uvicorn |
| Database | PostgreSQL 16, SQLAlchemy 2, asyncpg |
| Cache | Redis |
| Scraping | httpx, Playwright, Chromium |
| NLP | spaCy, pt_core_news_sm |
| Scheduling | APScheduler |
| Dashboard | Static HTML, CSS, JavaScript |

## License

[MIT](LICENSE)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md).
