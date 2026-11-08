"""Claude API prices in USD per million tokens (platform.claude.com pricing page, Nov 2026)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write_5m: float


PRICES = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125),
}
# Haiku 5.5 is dearer when the whole prompt is over 100k tokens.
HAIKU_LONG = Price(0.50, 2.50, 0.05, 0.625)


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    if model == "claude-haiku-5-5" and (input_tokens + cache_read + cache_write) > 100_000:
        price = HAIKU_LONG
    return (input_tokens * price.input + output_tokens * price.output
            + cache_read * price.cache_read + cache_write * price.cache_write_5m) / 1_000_000
