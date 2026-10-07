# ADR-005: Gradual traffic shifting

**Status:** Accepted

## Context
The evaluation pipeline scores provider quality, and those scores feed back into the gateway's routing
weights. A feedback loop that reacts to every score would hard-switch providers on noise, and a provider
cut to zero would never be sampled again, so it could never recover.

## Decision
The feedback loop never switches providers outright. All weight changes go through windowed aggregation:

- a 5-minute rolling window per route
- at least 50 evaluated requests before any change
- at most a 10% shift per window
- a 5% floor per provider, so quality alone never removes a provider

The new weights are written to Redis atomically by a Lua script. The gateway's router only reads
weights from Redis and picks a provider by weighted random choice; it never receives an instruction to
switch.

## Consequences
- A provider whose quality drops loses traffic over several windows, which bounds the damage from a
  noisy or wrong judge.
- Reliability failures are handled separately and faster: the circuit breaker (error rate above 5% over
  a 60-second window, after at least 10 requests) removes a failing provider immediately, whatever its
  weight.
- Recovery is slow by design.
