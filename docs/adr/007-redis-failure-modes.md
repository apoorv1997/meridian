# ADR-007: Redis fails open for rate limiting

**Status:** Accepted

## Context
Redis holds the token buckets for rate limiting, the routing weights and the circuit-breaker counters.
If rate limiting failed closed, a Redis outage would reject every request: a dependency failure would
become a full outage.

## Decision
Rate limiting fails open. The limiter checks Redis health before each operation. If Redis is
unreachable, it logs the failure, allows the request, marks it as unrated, and counts it in a metric for
later reconciliation. It never returns a rate-limit rejection because Redis is down.

Token deduction itself is a single Lua script (EVAL), so concurrent requests cannot double-spend a
bucket.

## Consequences
- During a Redis outage, tenants can exceed their limits until Redis returns. The metric shows how much.
- Availability wins over strict enforcement, which suits a gateway in front of paid model APIs: a short
  overage costs less than refusing every user.
- Integration tests cover rate limiting against a real Redis container.
