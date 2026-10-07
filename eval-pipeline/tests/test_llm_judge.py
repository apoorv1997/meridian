import json

import httpx
import pytest

from meridian_eval.judge.llm_judge import LLMJudge, _parse_score


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SCORE: 0.8", 0.8),
        ("Score: 0.8", 0.8),  # case doesn't matter
        ("SCORE: .8", 0.8),
        ("SCORE: 1", 1.0),
        ("Reasoning first.\nSCORE: 0.35", 0.35),
        ("SCORE: 7/10", 0.7),  # a fraction is read as a fraction
        ("SCORE: 8", -1.0),  # 0-10 scale: rejected, not clamped to 1.0
        ("SCORE: 3/0", -1.0),
        ("no score here", -1.0),
    ],
)
def test_parse_score(text: str, expected: float) -> None:
    assert _parse_score(text) == pytest.approx(expected)


def ollama(handler: object) -> LLMJudge:
    return LLMJudge(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))


async def test_score_sends_the_rubric_prompt_and_parses_the_reply() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "SCORE: 0.7"})

    judge = ollama(handler)
    score = await judge.score(rubric_name="faithfulness", query="Q?", response="A.", context="C.")
    await judge.close()
    assert score == 0.7
    assert seen["url"] == "http://ollama:11434/api/generate"
    body = seen["body"]
    assert body["model"] == "llama3.1:8b" and body["stream"] is False
    assert body["options"]["temperature"] == 0.0
    assert "Q?" in body["prompt"] and "A." in body["prompt"] and "C." in body["prompt"]


async def test_http_error_gives_minus_one() -> None:
    judge = ollama(lambda request: httpx.Response(500, text="model not loaded"))
    assert await judge.score(rubric_name="relevance", query="Q", response="A") == -1.0
    await judge.close()


async def test_connection_error_gives_minus_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    judge = ollama(handler)
    assert await judge.score(rubric_name="relevance", query="Q", response="A") == -1.0
    await judge.close()


async def test_unknown_rubric_raises() -> None:
    judge = ollama(lambda request: httpx.Response(200, json={"response": "SCORE: 1"}))
    with pytest.raises(ValueError, match="unknown rubric"):
        await judge.score(rubric_name="vibes", query="Q", response="A")
    await judge.close()
