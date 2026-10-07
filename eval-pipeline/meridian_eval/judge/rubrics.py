"""Evaluation rubrics for LLM-as-judge scoring.

Each rubric is a prompt fragment instructing the judge how to score the
response on a 0.0–1.0 scale. The judge must output a single float on its
own line, preceded by "SCORE:".
"""

FAITHFULNESS = """
You are evaluating whether a language model response is faithful to the
retrieved context provided to it.

Retrieved context:
<context>
{context}
</context>

User question:
<question>
{query}
</question>

Model response:
<response>
{response}
</response>

Score the faithfulness of the response on a scale from 0.0 to 1.0:
  1.0 = every factual claim in the response is directly supported by the context
  0.5 = most claims are supported but there are minor unsupported additions
  0.0 = the response contradicts the context or introduces unsupported facts

Output exactly one line in this format (nothing else):
SCORE: <float>
""".strip()

RELEVANCE = """
You are evaluating whether a language model response directly addresses the
user's question.

User question:
<question>
{query}
</question>

Model response:
<response>
{response}
</response>

Score the relevance of the response on a scale from 0.0 to 1.0:
  1.0 = response directly and completely answers the question
  0.5 = response partially answers the question or drifts off-topic
  0.0 = response is unrelated to the question

Output exactly one line in this format (nothing else):
SCORE: <float>
""".strip()

GROUNDEDNESS = """
You are evaluating whether a language model response avoids hallucination —
that is, whether it refrains from stating facts not present in the context.

Retrieved context:
<context>
{context}
</context>

Model response:
<response>
{response}
</response>

Score the groundedness of the response on a scale from 0.0 to 1.0:
  1.0 = no hallucinated facts; the model only uses information from the context
        or says it does not know
  0.5 = minor hallucination that does not meaningfully mislead
  0.0 = significant hallucination — facts invented without basis in the context

Output exactly one line in this format (nothing else):
SCORE: <float>
""".strip()

ALL_RUBRICS: dict[str, str] = {
    "faithfulness": FAITHFULNESS,
    "relevance": RELEVANCE,
    "groundedness": GROUNDEDNESS,
}

_EVIDENCE_TAGS = ("<context>", "<question>", "<response>")


def _reverse_evidence(template: str) -> str:
    """Return the template with its evidence sections (context, question, response) in
    reverse order. The instructions and the SCORE line stay where they are.

    Splitting happens on the template, before formatting, so blank lines inside the
    user's text can't affect it.
    """
    blocks = template.split("\n\n")
    evidence = [i for i, b in enumerate(blocks) if any(tag in b for tag in _EVIDENCE_TAGS)]
    reordered = list(blocks)
    for i, j in zip(evidence, reversed(evidence), strict=True):
        reordered[i] = blocks[j]
    return "\n\n".join(reordered)


# The ensemble's second judge call uses these, so the two calls see the evidence in
# opposite orders and a position preference in the judge averages out.
REVERSED_RUBRICS: dict[str, str] = {
    name: _reverse_evidence(template) for name, template in ALL_RUBRICS.items()
}
