from collections.abc import Callable

from meridian_eval.consumer.models import TraceSpan
from meridian_eval.tiers import tier1

SpanFactory = Callable[..., TraceSpan]


def test_normal_span_passes(make_span: SpanFactory) -> None:
    result = tier1.evaluate(make_span(attributes={"output_text": "Paris is the capital."}))
    assert result.passed
    assert result.latency_ok and result.format_ok and result.length_ok and result.toxicity_ok


def test_latency_over_the_sla_fails_only_latency(make_span: SpanFactory) -> None:
    result = tier1.evaluate(make_span(latency_ms=5_001.0))
    assert not result.passed
    assert not result.latency_ok and result.format_ok and result.length_ok


def test_latency_exactly_at_the_sla_passes(make_span: SpanFactory) -> None:
    assert tier1.evaluate(make_span(latency_ms=5_000.0)).latency_ok


def test_whitespace_only_output_fails_format(make_span: SpanFactory) -> None:
    assert not tier1.evaluate(make_span(attributes={"output_text": "  \n "})).format_ok


def test_span_without_output_text_passes_format(make_span: SpanFactory) -> None:
    # Gateway-only spans carry no text; there is nothing to check.
    assert tier1.evaluate(make_span(attributes={})).format_ok


def test_token_count_outside_the_range_fails_length(make_span: SpanFactory) -> None:
    assert not tier1.evaluate(make_span(output_tokens=0)).length_ok
    assert not tier1.evaluate(make_span(output_tokens=8_193)).length_ok
    assert tier1.evaluate(make_span(output_tokens=8_192)).length_ok


def test_toxicity_matches_whole_words_in_any_case(make_span: SpanFactory) -> None:
    def toxic(text: str) -> bool:
        return not tier1.evaluate(make_span(attributes={"output_text": text})).toxicity_ok

    assert toxic("That is SHIT.")
    assert toxic("just kill yourself")
    assert not toxic("Scunthorpe United won")  # keyword inside a word is not a match
    assert not toxic("I'll cook shitake mushrooms")
