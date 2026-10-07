# ADR-006: Tiered evaluation

**Status:** Accepted

## Context
Judging every response with a frontier model would cost about as much as serving the response. But
cheap checks alone miss quality problems such as unfaithful or ungrounded answers.

## Decision
Evaluate in tiers, each cheaper and broader than the one above it:

- **Tier 1, every span, synchronous in the consumer:** latency SLA, a non-empty output check, length
  anomalies, a toxicity keyword check.
- **Tier 2, a 10% sample by default, asynchronous:** a local Llama 3.1 8B judge served by Ollama scores
  faithfulness, relevance and groundedness. Each sample is judged twice, once with the evidence
  sections (context, question, response) in their normal order and once reversed, and the two scores
  are averaged, so a judge's preference for what it reads first or last cancels out. The prompts must
  differ: at temperature 0 the same prompt twice returns the same score twice.
- **Semantic drift detection:** outputs are embedded into a rolling window per route (the last 1,000
  outputs); an alert fires when the window's centroid moves past a per-route threshold from its baseline.

## Consequences
- Tier 2 runs on a local model, so it has no per-call API cost.
- An 8B judge is weaker than a frontier model; the two-order ensemble and the windowed feedback rules
  (ADR-005) limit how much one bad judgment can move routing.
- Tier 1, tier 2, the judge and the ensemble are tested. The drift detector and the feedback window
  are not yet, and per-route drift thresholds are not wired yet (every route uses the default).
