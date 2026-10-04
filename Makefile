.PHONY: data api test lint demo web e2e

data:
	cd backend && uv run python -m constellation.build

api:
	cd backend && uv run uvicorn constellation.api.app:app --host 0.0.0.0 --port 8000

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check constellation tests && uv run pyright

demo:
	@set -eu; \
	if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then \
		mode=$$(curl -fsS http://localhost:8000/api/health | python -c 'import json,sys; print(json.load(sys.stdin)["llm_mode"])'); \
		if [ "$$mode" != offline ]; then \
			echo "API is already running in $$mode mode; stop it and rerun make demo to guarantee offline mode."; \
			exit 1; \
		fi; \
	else \
		(cd backend && LLM_MODE=offline nohup uv run uvicorn constellation.api.app:app --host 0.0.0.0 --port 8000 >/dev/null 2>&1 </dev/null &); \
		curl --retry 30 --retry-connrefused --retry-delay 1 -fsS http://localhost:8000/api/health >/dev/null; \
	fi; \
	if curl -fsS http://localhost:3000 >/dev/null 2>&1; then \
		echo "Web app already responds on port 3000."; \
	else \
		(cd apps/web && NEXT_PUBLIC_API_BASE=http://localhost:8000 nohup pnpm dev --hostname 0.0.0.0 --port 3000 >/dev/null 2>&1 </dev/null &); \
		curl --retry 60 --retry-connrefused --retry-delay 1 -fsS http://localhost:3000 >/dev/null; \
	fi; \
	echo "Offline demo ready: API :8000, web :3000."

web:
	cd apps/web && NEXT_PUBLIC_API_BASE=http://localhost:8000 pnpm dev --hostname 0.0.0.0 --port 3000

e2e:
	@curl -fsS http://localhost:8000/api/health >/dev/null || { echo "Start the real API on :8000 before running make e2e."; exit 1; }
	cd apps/web && NEXT_PUBLIC_API_BASE=http://localhost:8000 pnpm e2e
