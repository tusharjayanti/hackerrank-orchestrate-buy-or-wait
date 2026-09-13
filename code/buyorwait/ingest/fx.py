"""Dated currency conversion using only the supplied exchange-rate rows."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from ..schemas.domain import ExchangeRate, FxConversion
from ..schemas.enums import Currency, FxMethod

ONE = Decimal(1)


class FxRateMissing(LookupError):
    """No supplied rate (direct, inverse or one-hop cross) exists for the pair on that date."""


class FxTable:
    def __init__(self, rates: Iterable[ExchangeRate]) -> None:
        self._rates: dict[tuple[date, Currency, Currency], Decimal] = {}
        for row in rates:
            self._rates[(row.rate_date, row.from_currency, row.to_currency)] = row.rate

    def _leg(self, source: Currency, target: Currency, on: date) -> tuple[Decimal, bool] | None:
        direct = self._rates.get((on, source, target))
        if direct is not None:
            return direct, False
        reverse = self._rates.get((on, target, source))
        if reverse is not None:
            return ONE / reverse, True
        return None

    def rate(self, source: Currency, target: Currency, on: date) -> FxConversion:
        if source == target:
            return FxConversion(rate=ONE, method=FxMethod.IDENTITY, rate_date=on, path=(source,))
        leg = self._leg(source, target, on)
        if leg is not None:
            value, inverted = leg
            method = FxMethod.INVERSE if inverted else FxMethod.DIRECT
            return FxConversion(rate=value, method=method, rate_date=on, path=(source, target))
        for middle in sorted(Currency):
            if middle in (source, target):
                continue
            first = self._leg(source, middle, on)
            second = self._leg(middle, target, on)
            if first is not None and second is not None:
                return FxConversion(
                    rate=first[0] * second[0], method=FxMethod.CROSS, rate_date=on, path=(source, middle, target)
                )
        raise FxRateMissing(f"no {source}->{target} rate on {on.isoformat()}")

    def convert(self, amount: Decimal, source: Currency, target: Currency, on: date) -> tuple[Decimal, FxConversion]:
        conversion = self.rate(source, target, on)
        return amount * conversion.rate, conversion
