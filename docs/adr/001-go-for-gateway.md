# ADR-001: Go for the gateway

**Status:** Accepted

## Context
Every LLM request passes through the gateway, so its own overhead is added to every call. It has to hold
many concurrent server-sent-event (SSE) streams open at once while it authenticates, rate-limits, checks
the cache and routes.

## Decision
Write the gateway in Go (1.22+), using Gin for HTTP, go-redis for Redis and pgx for PostgreSQL.
Routing overhead stays well under a millisecond, and each stream gets its own goroutine, so thousands of
open SSE connections are cheap.

The gateway stays a pure HTTP proxy: auth, caching, routing and forwarding. No Python and no ML
inference run inside it, and `cmd/server/main.go` only wires components together; it holds no business
logic. Anything that needs the Python ML ecosystem (embeddings, reranking, evaluation) lives in the RAG
engine or the evaluation pipeline and is called over HTTP or Kafka.

## Consequences
- The gateway and the ML services are deployed and scaled independently.
- Embedding a prompt for the semantic cache is a network call to the RAG engine. When the RAG engine
  is not configured, the gateway disables the cache and RAG retrieval rather than failing.
- Two languages in one repository: Go for the hot path, Python where the ML libraries are.
