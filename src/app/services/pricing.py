"""Token and call prices, in integer nano-dollars. 1 USD = 1_000_000_000 nanos."""

from dataclasses import dataclass

PRICING_VERSION = "2026-08-01"

# nanos per single token
INPUT_NANOS_PER_TOKEN = 300
CACHED_INPUT_NANOS_PER_TOKEN = 75
OUTPUT_NANOS_PER_TOKEN = 2500

# nanos per API call
API_CALL_NANOS = 100_000

NANOS_PER_CENT = 10_000_000


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


def token_cost_nanos(tokens: TokenUsage) -> int:
    """Each category is priced at its own rate; reasoning tokens bill as output."""
    return (
        tokens.input_tokens * INPUT_NANOS_PER_TOKEN
        + tokens.cached_input_tokens * CACHED_INPUT_NANOS_PER_TOKEN
        + tokens.output_tokens * OUTPUT_NANOS_PER_TOKEN
        + tokens.reasoning_tokens * OUTPUT_NANOS_PER_TOKEN
    )


def api_call_cost_nanos(calls: int = 1) -> int:
    return calls * API_CALL_NANOS


def nanos_to_cents(nanos: int) -> int:
    """Round half up, once, at the display boundary."""
    return (nanos + NANOS_PER_CENT // 2) // NANOS_PER_CENT
