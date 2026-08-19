.PHONY: install api web test lint archive-csv db-up db-down

install:
	uv sync --all-packages
	npm --prefix apps/web install

api:
	uv run --package find-next-api uvicorn find_next_api.main:app --reload --host 127.0.0.1 --port 8000

web:
	npm --prefix apps/web run dev

test:
	uv run --all-packages pytest

lint:
	uv run --all-packages ruff check .
	npm --prefix apps/web run lint

archive-csv:
	uv run --package find-next-pipeline archive-csv-data --delete-after-verify

db-up:
	docker compose up -d timescaledb

db-down:
	docker compose down
