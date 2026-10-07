.PHONY: up down logs test test-integration lint

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

# Unit tests; no Docker needed.
test:
	cd gateway && go test -short ./...
	cd rag-engine && .venv/bin/python -m pytest tests/
	cd eval-pipeline && .venv/bin/python -m pytest tests/

# Gateway integration tests (row-level security, Redis rate limiting, circuit breaker); needs Docker.
test-integration:
	cd gateway && go test ./tests/...

lint:
	cd gateway && golangci-lint run
	cd rag-engine && .venv/bin/ruff check .
	cd eval-pipeline && .venv/bin/ruff check .
