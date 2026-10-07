# Meridian

Self-hosted LLM infrastructure: an API gateway, a real-time RAG engine, and an evaluation pipeline whose
quality scores feed back into the gateway's routing.

```text
                 ┌──────────────────────── Gateway (Go) ────────────────────────┐
client ──HTTP──► │ auth → rate limit → semantic cache → RAG → weighted router  │ ──► LLM providers
                 └───────┬───────────────────────▲─────────────────────────────┘
                         │ traces (Kafka)         │ provider weights (Redis)
                         ▼                        │
                 ┌── Evaluation pipeline ──┐      │
                 │ tier 1 checks, sampled  │──────┘
                 │ LLM judge, drift alerts │
                 └─────────────────────────┘
documents ──Kafka──► RAG engine (chunk → embed → pgvector) ◄── retrieval ── Gateway
```

The point of the design is the closed loop: when a provider's measured quality drops, the evaluation
pipeline shifts traffic away from it gradually, without anyone switching it by hand.

## What's in it

**Gateway** (Go, Gin)
- API-key auth (SHA-256 hashed keys in PostgreSQL) and JWT-protected admin endpoints for key management
- Token-bucket rate limiting in Redis, atomic through a Lua script; fails open if Redis is down
- Semantic cache in pgvector, namespaced by model, system prompt, temperature bucket and tenant
- Weighted provider routing with a per-provider circuit breaker and failover
- Two proxy modes per route: SSE streaming passthrough, or buffered with exact cost tracking
- Prometheus metrics at `/metrics`

**RAG engine** (Python, FastAPI, Kafka)
- Kafka consumer with manual offset commits, content-hash idempotency and a dead-letter queue
- Fixed-size and semantic chunking
- Bounded embedding worker pool with backpressure; local sentence-transformers embeddings
- Every chunk records its embedding model and version; retrieval only compares vectors from the same model
- pgvector search, then cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`)

**Evaluation pipeline** (Python)
- Tier 1 checks on every trace: latency SLA, non-empty output, length anomalies, toxicity keywords
- Tier 2 on a 10% sample: a local Llama 3.1 8B judge (Ollama), run twice with shuffled option order
- Semantic drift detection over a rolling window of output embeddings
- Feedback into routing: 5-minute windows, at least 50 samples, at most a 10% shift, a 5% floor

**SDK** (Python): a `@trace_llm_call` decorator that sends traces from your own app into the pipeline.

All tables use PostgreSQL row-level security for tenant isolation.

## Status

| Part | State |
|---|---|
| Gateway | Built. Unit tests for auth, cache, proxy and routing; integration tests for row-level security, rate limiting and the circuit breaker |
| RAG engine | Built. 35 tests |
| Evaluation pipeline | Code written; tests not yet written |
| SDK | Code written; tests not yet written |
| Load benchmarks (k6) | Not yet run, so this README quotes no latency numbers |
| Streaming-mode cost reconciliation | Designed ([ADR-010](docs/adr/010-streaming-vs-buffer-mode.md)), not yet implemented; buffer mode records exact cost |

## Run it locally

Requirements: Docker, Go 1.22+, Python 3.11+.

```bash
cp .env.example .env            # add the provider API keys you want to use
make up                         # PostgreSQL + pgvector, Redis, Kafka, Ollama, Prometheus, Grafana

cd gateway && go run ./cmd/server                                    # gateway on :8080
cd rag-engine && pip install -e . && python -m meridian_rag.api.server   # RAG API on :8090
cd rag-engine && python -m meridian_rag.consumer.kafka_consumer          # document ingestion
cd eval-pipeline && pip install -e . && python -m meridian_eval.consumer.trace_consumer
```

The gateway runs without the RAG service; it then disables RAG retrieval and the semantic cache and logs
a warning.

## Tests

```bash
make test               # unit tests: gateway (go test -short) and the RAG engine; no Docker needed
make test-integration   # gateway integration tests against real PostgreSQL and Redis containers
```

## Design decisions

Each one is written up as an architecture decision record in [`docs/adr`](docs/adr):

1. [Go for the gateway](docs/adr/001-go-for-gateway.md)
2. [Kafka as the backbone](docs/adr/002-kafka-as-backbone.md)
3. [pgvector instead of a dedicated vector database](docs/adr/003-pgvector-over-dedicated-vectordb.md)
4. [Composite semantic-cache key](docs/adr/004-composite-cache-key.md)
5. [Gradual traffic shifting](docs/adr/005-gradual-traffic-shifting.md)
6. [Tiered evaluation](docs/adr/006-tiered-evaluation.md)
7. [Redis fails open for rate limiting](docs/adr/007-redis-failure-modes.md)
8. [Embedding model versioning](docs/adr/008-embedding-model-versioning.md)
9. [Row-level security for multi-tenancy](docs/adr/009-row-level-security.md)
10. [Streaming and buffer proxy modes](docs/adr/010-streaming-vs-buffer-mode.md)

## Layout

```text
gateway/          Go API gateway (cmd/server, internal/*, tests/)
rag-engine/       Kafka ingestion, embedding workers, retrieval API
eval-pipeline/    trace consumer, evaluation tiers, judge, drift, feedback
sdk/              Python tracing SDK
infra/            PostgreSQL schema, Prometheus config
docs/adr/         architecture decision records
```

License: MIT (see `LICENSE`).
