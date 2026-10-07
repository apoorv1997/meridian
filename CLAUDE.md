# Working on Meridian

Context for anyone, human or AI assistant, picking up development. Read `README.md` for what Meridian is
and `docs/adr/` for why it is built this way. This file covers how to work on it and what is next.

Repository: `github.com/apoorv1997/meridian`, branch `main`. Work in this checkout.

## Setup

```bash
cp .env.example .env        # add provider keys; never commit .env
for svc in rag-engine eval-pipeline sdk; do
  (cd $svc && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]")
done
make up                     # infrastructure only (docker-compose); app services run on the host
```

- Go module: `github.com/apoorv1997/meridian/gateway`, Go 1.25 (see `gateway/go.mod`).
- Python 3.11+, one virtualenv per service (`rag-engine/.venv`, `eval-pipeline/.venv`, `sdk/.venv`).
- Ports: gateway 8080, RAG API 8090, PostgreSQL 5432, Redis 6379, Kafka 9094 from the host
  (9092 inside Docker), Ollama 11434, Prometheus 9090, Grafana 3000.

## Commands

| Task | Command |
|---|---|
| Unit tests (no Docker) | `make test`, i.e. `go test -short ./...` in `gateway/` and pytest in `rag-engine/` and `eval-pipeline/` |
| Gateway integration tests | `make test-integration` (testcontainers; needs Docker running) |
| One Go package | `cd gateway && go test -short ./internal/cache/` |
| One Python service | `cd rag-engine && .venv/bin/python -m pytest tests/ -q` |
| Lint | `make lint` (golangci-lint, ruff) |
| Run the gateway | `cd gateway && go run ./cmd/server` |
| Run the RAG API | `cd rag-engine && .venv/bin/python -m meridian_rag.api.server` |
| Run ingestion | `cd rag-engine && .venv/bin/python -m meridian_rag.consumer.kafka_consumer` |
| Run evaluation | `cd eval-pipeline && .venv/bin/python -m meridian_eval.consumer.trace_consumer` |

## Architecture rules

Each is an ADR in `docs/adr/`. Do not break one without writing a new ADR that supersedes it.

- **ADR-001:** the gateway is a pure Go proxy (auth, cache, routing, forwarding). No Python, no ML
  inference, no business logic in `cmd/server/main.go`.
- **ADR-004:** never cache on the prompt embedding alone. The namespace is
  `sha256(model + system_prompt + temperature_bucket + tenant_id)`. A cross-tenant hit is a security bug.
- **ADR-005:** routing weights change only through the windowed feedback loop
  (`eval-pipeline/meridian_eval/feedback/`): 5-minute window, at least 50 samples, each delta clamped
  to 10%, a 5% floor enforced after renormalising. Weights live in `route:{route_id}:weights`; if you
  change that format, change `route_weights_key()` and the gateway's `weightsKey()` together (each has
  a test pinning it). The gateway only reads weights from Redis.
- **ADR-007:** rate limiting fails open. On a Redis error: log, allow, count it in
  `RateLimitFailOpenTotal`. Never reject a request because Redis is down.
- **ADR-008:** every chunk carries `embedding_model` and `embedding_version`; every retrieval query
  filters by `embedding_model`.
- **ADR-009:** every table has `tenant_id`, row-level security and a `tenant_isolation` policy before
  any code touches it. Connections set `app.tenant_id`. Schema lives in `infra/docker/postgres/init.sql`.
- **ADR-010:** only buffer-mode routes use the semantic cache.

## Conventions

**Go:** `gofmt`; wrap errors with context (`fmt.Errorf("auth: %w", err)`); no `panic` on request paths;
`context.Context` first on anything that touches I/O; `log/slog` with JSON in production; table-driven
unit tests next to the source; Docker-backed tests in `gateway/tests/` must call
`if testing.Short() { t.Skip("requires docker") }` so `make test` stays Docker-free.

**Python:** `ruff`; type hints on every signature; async I/O (`asyncio`, `aiokafka`, `asyncpg`);
Pydantic models for anything crossing a service boundary; docstrings on public functions; tests in each
service's `tests/` with `pytest-asyncio`; Kafka consumers commit offsets manually, after the write.

## Current state (verified 2026-10-07)

| Part | State |
|---|---|
| Gateway | Built. Unit tests pass in `auth`, `cache`, `proxy`, `router`; integration tests for RLS, rate limiting and the circuit breaker (Docker) |
| RAG engine | Built. 35 tests pass |
| Evaluation pipeline | 62 tests: weight updater (Lua through fakeredis), feedback window, tier 1, tier 2, judge parsing and HTTP, the two-order ensemble, the drift detector, and the Go-to-Python span contract. Lint clean |
| SDK | Code written (`@trace_llm_call`, tracer, exporters). **No tests**, no README or examples |
| Benchmarks | **None.** No k6 scripts exist yet |
| Streaming cost reconciliation | **Not implemented.** Only a comment in `gateway/internal/proxy/streaming.go` |
| Dockerfiles / app services in compose | **None.** docker-compose runs infrastructure only |
| Grafana | No dashboards or provisioning yet (`infra/grafana/` is empty) |
| CI | **None** |

Known quirks:
- `GATEWAY_ADMIN_PORT` (8081) is loaded in `internal/config/loader.go` but unused; admin routes are
  served under `/admin` on the main port, behind JWT.
- Without `RAG_SERVICE_URL`, the gateway disables RAG retrieval and the semantic cache and logs a warning.
- Prometheus scrapes the gateway at `host.docker.internal:8080` because the gateway runs on the host.
- Tier 1's `format` check only verifies the output is non-empty; it does not validate a schema.
- The integration tests use testcontainers and fail (not skip) without Docker unless run with `-short`.

## Next work, in order

1. **Evaluation pipeline, continued** (the weight updater and the gateway's weight handling are done):
   - load `route_configs.drift_threshold` into the drift detector (`set_threshold(tenant, route, x)`);
     the table has row-level security, so reading every tenant's config needs a deliberate choice
     (a per-tenant query with `app.tenant_id` set, or a role allowed to read across tenants);
   - the RAG engine has 46 pre-existing ruff findings (mostly missing type annotations), so
     `make lint` fails until they're fixed.
2. **Routing weights are shared across tenants.** The Redis key is `route:{route_id}:weights` with no
   tenant, but route IDs are only unique within a tenant (`route_configs` is UNIQUE(tenant_id,
   route_id)). Tenant A's evaluation scores therefore move tenant B's routing on a route with the same
   name. Fix both sides together: put the tenant in the key in `weightsKey()` and
   `route_weights_key()`, key the feedback window by tenant, and update ADR-005.
3. **SDK tests**, a short `sdk/README.md`, and one example in `sdk/examples/`.
4. **k6 benchmarks** in `benchmarks/k6/scripts/`: gateway only, gateway with RAG, full stack. Record
   p50/p95/p99 in `benchmarks/README.md`. To claim gateway overhead, measure it against a direct call to
   the same provider (or a mock provider), not against a guess.
5. **Stream-end cost reconciliation** in `streaming.go`: read `finish_reason` and usage from the final
   SSE event and write the exact cost record, per ADR-010.
6. **Dockerfiles** for the gateway, RAG engine and evaluation pipeline, added as compose services.
7. **Grafana** datasource provisioning and a gateway dashboard (request rate, latency, cache hit rate,
   fail-open count, provider weights).
8. **CI** on GitHub Actions: `go test -short ./...`, pytest for each Python service, ruff, golangci-lint.
9. **End-to-end demo:** ingest a document, ask a question through the gateway, show the trace, an
   evaluation score and a routing-weight change.

## Ground rules

- Keep the README's status table true. A number goes in the README only after it has been measured, and
  the README says how it was measured.
- Never commit `.env`, keys, or local tool settings.
- A new table needs RLS and its policy in the same change (ADR-009).
- Commits are authored by the repository owner; do not add co-author trailers.
