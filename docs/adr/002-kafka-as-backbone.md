# ADR-002: Kafka as the backbone

**Status:** Accepted

## Context
Three kinds of work flow between the layers: document events into the RAG engine, request traces out of
the gateway, and evaluation results back toward routing. None of them should slow down or break the
request path, and failed work has to be inspectable and replayable.

## Decision
Use Apache Kafka 3.8 in KRaft mode (no ZooKeeper) with four topics:

| Topic | Partitions | Carries |
|---|---|---|
| `meridian.documents` | 4 | document create, update and delete events |
| `meridian.traces` | 4 | spans emitted by the gateway |
| `meridian.evaluations` | 2 | evaluation results |
| `meridian.dlq` | 2 | failed messages, with the original payload, error and retry count |

Consumers commit offsets manually, and only after their write succeeds.

## Consequences
- **Audit trail and replay:** every document and trace is retained and can be reprocessed, for example
  after a chunking or embedding change.
- **Decoupled failure modes:** if the evaluation pipeline is down, the gateway keeps serving and traces
  wait in the topic.
- Kafka is the heaviest piece of the local stack; docker-compose runs it as a single KRaft node.
