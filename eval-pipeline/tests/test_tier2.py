from collections.abc import Callable

from meridian_eval.consumer.models import TraceSpan
from meridian_eval.judge.ensemble import EnsembleResult
from meridian_eval.tiers import tier2

SpanFactory = Callable[..., TraceSpan]

TEXT = {"input_text": "What is the capital of France?", "output_text": "Paris."}


class FakeJudge:
    """Stands in for EnsembleJudge: returns fixed scores per rubric, or raises."""

    def __init__(self, scores: dict[str, float] | None = None, fail: bool = False) -> None:
        self.scores = scores or {}
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def score_all(self, names: list[str], **kwargs: object) -> dict[str, EnsembleResult]:
        self.calls.append({"names": names, **kwargs})
        if self.fail:
            raise RuntimeError("ollama down")
        return {n: EnsembleResult(n, self.scores[n], self.scores[n], self.scores[n]) for n in names}


async def test_unsampled_span_never_reaches_the_judge(make_span: SpanFactory) -> None:
    judge = FakeJudge()
    result = await tier2.evaluate(make_span(attributes=TEXT), judge, sample_rate=0.0)
    assert not result.sampled and result.skipped_reason == "not_sampled"
    assert judge.calls == []


async def test_sampled_span_without_text_is_skipped(make_span: SpanFactory) -> None:
    judge = FakeJudge()
    result = await tier2.evaluate(make_span(attributes={}), judge, sample_rate=1.0)
    assert result.sampled and result.skipped_reason == "no_text_attributes"
    assert judge.calls == []


async def test_aggregate_is_the_mean_of_valid_rubric_scores(make_span: SpanFactory) -> None:
    judge = FakeJudge({"faithfulness": 0.9, "relevance": 0.6, "groundedness": -1.0})
    span = make_span(attributes={**TEXT, "rag_context": "France: capital Paris."})
    result = await tier2.evaluate(span, judge, sample_rate=1.0)
    assert result.aggregate_score == 0.75  # the failed rubric (-1.0) is left out
    assert judge.calls[0]["context"] == "France: capital Paris."


async def test_no_valid_scores_gives_minus_one(make_span: SpanFactory) -> None:
    judge = FakeJudge({"faithfulness": -1.0, "relevance": -1.0, "groundedness": -1.0})
    result = await tier2.evaluate(make_span(attributes=TEXT), judge, sample_rate=1.0)
    assert result.aggregate_score == -1.0


async def test_judge_failure_is_reported_not_raised(make_span: SpanFactory) -> None:
    span = make_span(attributes=TEXT)
    result = await tier2.evaluate(span, FakeJudge(fail=True), sample_rate=1.0)
    assert result.sampled and result.skipped_reason.startswith("judge_error:")


def test_sampling_rate_is_roughly_honoured() -> None:
    hits = sum(tier2.should_sample(0.1) for _ in range(20_000))
    assert 1_700 < hits < 2_300
