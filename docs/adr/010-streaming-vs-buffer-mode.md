# ADR-010: Streaming and buffer proxy modes

**Status:** Accepted

## Context
Interactive clients want tokens as soon as they're generated. Caching and exact cost accounting need
the complete response. One proxy mode can't serve both.

## Decision
Offer two modes, configured per route:

- **Passthrough (streaming):** tokens are copied to the client over SSE as they arrive. Cost is estimated
  up front and reconciled at the end of the stream from `finish_reason` and the provider's usage metadata.
- **Buffer:** the full response is collected, its exact cost computed, and then it is returned. This is
  the only mode that can read from or write to the semantic cache.

## Consequences
- Streaming routes bypass the semantic cache automatically.
- Cost records for streaming routes are estimates until the stream ends. Stream-end reconciliation is
  designed but not yet implemented in `internal/proxy/streaming.go`; buffer mode records exact cost today.
- Routes choose per use case: chat UIs stream, while batch and repeated-prompt workloads buffer and
  benefit from the cache.
