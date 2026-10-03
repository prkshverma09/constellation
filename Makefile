.PHONY: data api test lint demo web

data:
	cd backend && uv run python -m constellation.build

api:
	cd backend && uv run uvicorn constellation.api.app:app --host 0.0.0.0 --port 8000

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check constellation tests && uv run pyright

demo:
	@echo "Frontend demo target will be added in the frontend handoff."

web:
	@echo "Frontend target will be added in the frontend handoff."
