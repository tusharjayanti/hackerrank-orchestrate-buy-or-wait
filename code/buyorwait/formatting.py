"""Output formatting for amounts, plans and dates, matching sample_requests.csv conventions."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def quantize_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def format_plan_amount(value: Decimal) -> str:
    """Whole amounts without decimals, otherwise exactly two decimals (25256, 620.40)."""
    cents = quantize_cents(value)
    return str(int(cents)) if cents == cents.to_integral_value() else f"{cents:.2f}"


def format_safe_amount(value: Decimal) -> str:
    """Numeric column: at most two decimals, trailing zeros dropped (17229139.2, 25256)."""
    text = f"{quantize_cents(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_money(value: Decimal, currency: str) -> str:
    cents = quantize_cents(value)
    body = f"{int(cents):,}" if cents == cents.to_integral_value() else f"{cents:,.2f}"
    return f"{currency} {body}"


def format_long_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"
