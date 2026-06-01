.PHONY: up scrape test lint

up:
	docker compose up -d

scrape:
	docker compose exec api python -c "from app.scheduler.jobs import run_full_pipeline; import asyncio; asyncio.run(run_full_pipeline())"

test:
	docker compose run --rm api pytest --cov=app

lint:
	ruff check . && mypy app/
