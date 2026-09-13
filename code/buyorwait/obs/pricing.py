"""Anthropic first-party list prices (USD per million tokens) for cost estimates."""

from __future__ import annotations

from dataclasses import dataclass

CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache writes
CACHE_READ_MULTIPLIER = 0.10
BATCH_DISCOUNT = 0.50  # Message Batches API price relative to synchronous calls


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5": ModelPrice(2.00, 10.00),
    "claude-opus-5": ModelPrice(5.00, 25.00),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
}


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    batch: bool = False,
) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    input_cost = (
        input_tokens
        + cache_creation_input_tokens * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * CACHE_READ_MULTIPLIER
    ) * price.input_per_mtok
    cost = (input_cost + output_tokens * price.output_per_mtok) / 1_000_000
    return cost * BATCH_DISCOUNT if batch else cost
