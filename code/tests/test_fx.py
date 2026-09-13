from datetime import date
from decimal import Decimal

import pytest

from buyorwait.ingest.fx import FxRateMissing, FxTable
from buyorwait.schemas.domain import ExchangeRate
from buyorwait.schemas.enums import Currency, FxMethod

DAY = date(2024, 1, 15)


@pytest.fixture
def table() -> FxTable:
    return FxTable(
        [
            ExchangeRate(rate_date=DAY, from_currency="USD", to_currency="INR", rate="83"),
            ExchangeRate(rate_date=DAY, from_currency="EUR", to_currency="USD", rate="1.1"),
        ]
    )


def test_identity(table):
    amount, conversion = table.convert(Decimal("10"), Currency.INR, Currency.INR, DAY)
    assert amount == Decimal("10") and conversion.method is FxMethod.IDENTITY


def test_direct_rate(table):
    amount, conversion = table.convert(Decimal("10"), Currency.USD, Currency.INR, DAY)
    assert amount == Decimal("830") and conversion.method is FxMethod.DIRECT


def test_inverse_rate(table):
    amount, conversion = table.convert(Decimal("83"), Currency.INR, Currency.USD, DAY)
    assert amount.quantize(Decimal("0.000001")) == Decimal("1.000000")
    assert conversion.method is FxMethod.INVERSE


def test_cross_rate(table):
    amount, conversion = table.convert(Decimal("10"), Currency.EUR, Currency.INR, DAY)
    assert amount == Decimal("913.0") and conversion.method is FxMethod.CROSS
    assert conversion.path == (Currency.EUR, Currency.USD, Currency.INR)


def test_rate_on_other_date_is_missing(table):
    with pytest.raises(FxRateMissing):
        table.rate(Currency.USD, Currency.INR, date(2024, 1, 16))


def test_every_foreign_event_converts_with_a_direct_rate(dataset):
    for event in dataset.events:
        home = dataset.profiles[event.user_id].home_currency
        if event.currency != home:
            assert dataset.fx.rate(event.currency, home, event.cash_date).method is FxMethod.DIRECT
