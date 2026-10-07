# ADR-008: Embedding model versioning

**Status:** Accepted

## Context
Embedding models change. Vectors from different models, or from different versions of one model, live
in different spaces, so comparing them gives meaningless similarity scores. Without a record of which
model produced each vector, a model upgrade silently corrupts retrieval.

## Decision
Every chunk written to pgvector carries `embedding_model` and `embedding_version`. The chunk model
rejects a chunk without both. Every retrieval query filters by `embedding_model`, so search only
compares vectors from the same model. Each chunk also stores a SHA-256 content hash, so re-ingesting
unchanged content is a no-op.

Embeddings run locally with sentence-transformers: no per-call API cost, and full control over which
model version is in use.

## Consequences
- A model upgrade can run side by side: new chunks get the new model while old ones stay queryable.
- Switching models fully requires re-embedding, which Kafka replay (ADR-002) makes possible.
