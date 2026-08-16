"""Pinned pricing tests. Changing a price must break these deliberately, never silently."""

from app.services.pricing import (
    API_CALL_NANOS,
    CACHED_INPUT_NANOS_PER_TOKEN,
    INPUT_NANOS_PER_TOKEN,
    OUTPUT_NANOS_PER_TOKEN,
    TokenUsage,
    api_call_cost_nanos,
    nanos_to_cents,
    token_cost_nanos,
)


def test_pinned_rates():
    assert INPUT_NANOS_PER_TOKEN == 300
    assert CACHED_INPUT_NANOS_PER_TOKEN == 75
    assert OUTPUT_NANOS_PER_TOKEN == 2500
    assert API_CALL_NANOS == 100_000


def test_input_tokens_priced_at_input_rate():
    assert token_cost_nanos(TokenUsage(input_tokens=1000)) == 300_000


def test_cached_input_is_cheaper_than_fresh_input():
    fresh = token_cost_nanos(TokenUsage(input_tokens=1000))
    cached = token_cost_nanos(TokenUsage(cached_input_tokens=1000))

    assert cached == 75_000
    assert cached < fresh


def test_reasoning_tokens_are_billed_at_the_output_rate():
    reasoning = token_cost_nanos(TokenUsage(reasoning_tokens=400))
    output = token_cost_nanos(TokenUsage(output_tokens=400))

    assert reasoning == output == 1_000_000


def test_categories_are_priced_separately_not_summed():
    """Summing token counts first would produce a different, wrong answer."""
    tokens = TokenUsage(
        input_tokens=1000,
        cached_input_tokens=2000,
        output_tokens=500,
        reasoning_tokens=300,
    )

    expected = (
        1000 * 300      # input
        + 2000 * 75     # cached input, discounted
        + 500 * 2500    # output
        + 300 * 2500    # reasoning, at the output rate
    )
    assert token_cost_nanos(tokens) == expected == 2_450_000

    naive_total_at_input_rate = tokens.total * INPUT_NANOS_PER_TOKEN
    assert naive_total_at_input_rate != token_cost_nanos(tokens)


def test_zero_usage_costs_nothing():
    assert token_cost_nanos(TokenUsage()) == 0


def test_api_call_cost():
    assert api_call_cost_nanos() == 100_000
    assert api_call_cost_nanos(7) == 700_000


def test_full_request_cost():
    tokens = TokenUsage(
        input_tokens=1000, cached_input_tokens=500, output_tokens=200, reasoning_tokens=50
    )
    total = api_call_cost_nanos() + token_cost_nanos(tokens)

    assert total == 100_000 + (300_000 + 37_500 + 500_000 + 125_000)
    assert total == 1_062_500


def test_cents_conversion_rounds_half_up_once():
    assert nanos_to_cents(0) == 0
    assert nanos_to_cents(10_000_000) == 1
    assert nanos_to_cents(4_999_999) == 0
    assert nanos_to_cents(5_000_000) == 1
    assert nanos_to_cents(1_234_500_000) == 123
