# ADR-005: Gradual traffic shifting

**Status:** Accepted

## Context
The evaluation pipeline scores provider quality, and those scores feed back into the gateway's routing
weights. A feedback loop that reacts to every score would hard-switch providers on noise, and a provider
cut to zero would never be sampled again, so it could never recover.

## Decision
The feedback loop never switches providers outright. All weight changes go through windowed aggregation:

- a 5-minute rolling window per route
- at least 50 evaluated requests per provider before its weight changes
- each provider's delta clamped to 10% per window
- a 5% floor per provider, enforced after renormalising, so quality alone never removes a provider

One Lua script applies the update atomically: it reads every provider on the route, applies the
clamped deltas, renormalises the whole route to sum to 1.0, then raises any provider under the floor
and rescales the rest, repeating until none is newly floored. A provider with no stored weight starts
at an equal share.

Weights live in the Redis hash `route:{route_id}:weights`. The eval pipeline writes it
(`route_weights_key()` in `feedback/weight_updater.py`) and the gateway reads it (`weightsKey()` in
`internal/router/weighted.go`); a test on each side pins the format. The gateway's router only reads
these weights and picks a provider by weighted random choice; it never receives an instruction to
switch. If it finds a weight under the floor, it raises it to the floor rather than treating it as
missing.

## Consequences
- A provider whose quality drops loses traffic over several windows, which bounds the damage from a
  noisy or wrong judge.
- Reliability failures are handled separately and faster: the circuit breaker (error rate above 5% over
  a 60-second window, after at least 10 requests) removes a failing provider immediately, whatever its
  weight.
- Recovery is slow by design.
