# ADR-004: Composite semantic-cache key

**Status:** Accepted

## Context
A semantic cache answers a new prompt with a stored response when the prompts are similar enough. Keying
on the prompt embedding alone is unsafe: two prompts can be semantically close while differing in model,
system prompt, sampling temperature or tenant, and each of those changes what the right answer is.

## Decision
Never cache on the prompt embedding alone. Every cache entry lives in a namespace:

```
namespace = sha256(model_id + system_prompt + temperature_bucket + tenant_id)
```

Temperature is rounded to one decimal place, so 0.70 and 0.71 share a bucket. Similarity search runs
only inside that namespace. Entries record the source document IDs they were built from, so updating a
document invalidates the entries that depend on it.

## Consequences
- A cache hit can never cross tenants. A hit in the wrong tenant's namespace would be a security
  incident, not a cache bug.
- The namespace hash hides the raw system prompt and tenant ID; a unit test checks that neither leaks
  into the key.
- Fewer cross-namespace hits than embedding-only caching. That is the intended trade.
