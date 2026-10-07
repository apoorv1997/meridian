# ADR-003: pgvector instead of a dedicated vector database

**Status:** Accepted

## Context
Meridian stores relational data (tenants, API keys, route configs, cost records, evaluation results) and
vectors (document chunks, semantic-cache prompts, drift embeddings). Every row belongs to a tenant and
must be isolated by tenant.

## Decision
Use PostgreSQL 16 with pgvector 0.7 as the single database for both relational and vector data.

Vectors live in three separate HNSW indexes, never mixed:
- `chunks_embedding_idx`: RAG document vectors, tuned for read recall
- `cache_embedding_idx`: semantic-cache vectors, which turn over quickly and vacuum separately
- `drift_embedding_idx`: evaluation output vectors for drift detection

## Consequences
- One database to run, back up and secure.
- Row-level security (ADR-009) covers vectors and relational rows the same way, so tenant isolation is
  enforced in one place.
- Vector search, joins and tenant filters happen in one query.
- Separate indexes keep the cache's write churn from degrading document-retrieval recall.
