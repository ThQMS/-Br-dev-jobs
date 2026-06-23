# Contributing

Thanks for considering a contribution to `br-dev-jobs`.

## Development Setup

Requirements:

- Python 3.12
- Docker and Docker Compose
- Make

Start the full local stack:

```bash
cp .env.example .env
docker compose up --build -d
```

Run the API at `http://localhost:8000`, the dashboard at `/`, and the interactive API docs at `/api/docs`.

## Local Python Environment

For local checks outside Docker:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS/Linux, activate the environment with:

```bash
source .venv/bin/activate
```

## Quality Checks

Run these before opening a pull request:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy app
python -m pytest
```

Docker equivalents:

```bash
make lint
make test
```

## Pull Requests

- Keep changes focused and scoped to one problem.
- Add or update tests for behavior changes.
- Update docs when setup, API behavior, or architecture changes.
- Use clear commit messages, for example `Add salary insight filters`.

## Issues

Use the bug report or feature request templates when opening issues. Include enough context for maintainers to reproduce the behavior or understand the proposed change.
