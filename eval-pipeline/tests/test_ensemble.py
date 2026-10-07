import json

import httpx
import pytest

from meridian_eval.judge.ensemble import EnsembleJudge
from meridian_eval.judge.llm_judge import LLMJudge
from meridian_eval.judge.rubrics import ALL_RUBRICS, REVERSED_RUBRICS

TAGS = ("<context>", "<question>", "<response>")


def tag_order(prompt: str) -> list[str]:
    return sorted((t for t in TAGS if t in prompt), key=prompt.index)


@pytest.mark.parametrize("name", sorted(ALL_RUBRICS))
def test_reversed_rubric_only_reorders_the_evidence(name: str) -> None:
    normal, reversed_ = ALL_RUBRICS[name], REVERSED_RUBRICS[name]
    assert tag_order(reversed_) == list(reversed(tag_order(normal)))
    assert sorted(normal.split("\n\n")) == sorted(reversed_.split("\n\n"))  # nothing added or lost
    assert reversed_.split("\n\n")[0] == normal.split("\n\n")[0]  # instructions first
    assert reversed_.endswith("SCORE: <float>")


def ollama_replying(scores: dict[str, str], prompts: list[str]) -> LLMJudge:
    """A judge whose reply depends on which evidence section comes first in the prompt."""

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["prompt"]
        prompts.append(prompt)
        return httpx.Response(200, json={"response": scores[tag_order(prompt)[0]]})

    return LLMJudge(transport=httpx.MockTransport(handler))


async def test_the_two_calls_see_the_evidence_in_opposite_orders() -> None:
    prompts: list[str] = []
    judge = ollama_replying({"<context>": "SCORE: 0.9", "<response>": "SCORE: 0.5"}, prompts)
    result = await EnsembleJudge(judge).score(
        rubric_name="faithfulness", query="Q?", response="A.", context="C."
    )
    await judge.close()
    assert len(prompts) == 2 and prompts[0] != prompts[1]
    assert {tag_order(p)[0] for p in prompts} == {"<context>", "<response>"}
    # A judge that prefers whatever it reads first is averaged out: (0.9 + 0.5) / 2.
    assert (result.score_a, result.score_b, result.score) == pytest.approx((0.9, 0.5, 0.7))


async def test_one_failed_call_falls_back_to_the_other() -> None:
    judge = ollama_replying({"<context>": "SCORE: 0.8", "<response>": "no score"}, [])
    result = await EnsembleJudge(judge).score(
        rubric_name="faithfulness", query="Q", response="A", context="C"
    )
    await judge.close()
    assert result.score == pytest.approx(0.8)


async def test_both_calls_failing_gives_minus_one() -> None:
    judge = ollama_replying({"<context>": "garbage", "<response>": "garbage"}, [])
    result = await EnsembleJudge(judge).score(
        rubric_name="faithfulness", query="Q", response="A", context="C"
    )
    await judge.close()
    assert result.score == -1.0


async def test_score_all_runs_every_rubric_twice() -> None:
    prompts: list[str] = []
    judge = ollama_replying(
        {"<context>": "SCORE: 1", "<question>": "SCORE: 1", "<response>": "SCORE: 1"}, prompts
    )
    results = await EnsembleJudge(judge).score_all(
        sorted(ALL_RUBRICS), query="Q", response="A", context="C"
    )
    await judge.close()
    assert set(results) == set(ALL_RUBRICS)
    assert len(prompts) == 2 * len(ALL_RUBRICS)
