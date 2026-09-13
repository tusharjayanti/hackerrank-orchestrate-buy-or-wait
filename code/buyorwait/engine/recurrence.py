"""Detect recurring income and expense series from settled history."""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from pydantic import BaseModel, ConfigDict

from ..schemas.domain import LedgerEntry
from ..schemas.enums import CashTreatment, Direction, EventType, Flexibility, LifecycleRole
from .knobs import EngineKnobs, Estimator, Rounding

NON_RECURRING_TYPES = {
    EventType.INVESTMENT_PURCHASE,
    EventType.INVESTMENT_SALE,
    EventType.INVESTMENT_VALUATION,
    EventType.REFUND,
}


def add_months(day: date, months: int, anchor_day: int | None = None) -> date:
    year, month_index = divmod(day.month - 1 + months, 12)
    year += day.year
    month = month_index + 1
    return date(year, month, min(anchor_day or day.day, calendar.monthrange(year, month)[1]))


class RecurringSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    event_type: EventType
    direction: Direction
    category: str
    description: str
    flexibility: Flexibility
    cadence_days: int
    monthly: bool
    anchor_day: int
    last_date: date
    estimate: Decimal
    constant_amount: bool
    occurrences: int
    event_ids: tuple[str, ...]
    minimum_allowed_amount: Decimal | None

    @property
    def latest_event_id(self) -> str:
        return self.event_ids[-1]

    @property
    def signed_estimate(self) -> Decimal:
        return self.estimate if self.direction is Direction.CREDIT else -self.estimate

    def dates_between(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        step = 1
        while True:
            if self.monthly:
                day = add_months(self.last_date, step, self.anchor_day)
            else:
                day = self.last_date + timedelta(days=self.cadence_days * step)
            step += 1
            if day > end:
                return days
            if day >= start:
                days.append(day)


def group_history(entries: Iterable[LedgerEntry], as_of: date, knobs: EngineKnobs) -> dict[str, list[LedgerEntry]]:
    """Group settled history into candidate series.

    Spending rotates descriptions within a category, so debits group by category and flexibility.
    Income groups by description when a description repeats (payroll); otherwise a category's varied
    credits (e.g. freelance payments) are pooled, and lone one-offs next to a payroll are dropped.
    """
    groups: dict[str, list[LedgerEntry]] = defaultdict(list)
    credits_by_category: dict[tuple[EventType, str], list[LedgerEntry]] = defaultdict(list)
    for entry in entries:
        if not is_history(entry, as_of):
            continue
        if entry.direction is Direction.CREDIT:
            credits_by_category[(entry.event_type, entry.category)].append(entry)
        else:
            groups[f"{entry.event_type}|debit|{entry.category}|{entry.flexibility}"].append(entry)

    for (event_type, category), credits in credits_by_category.items():
        counts = Counter(entry.description for entry in credits)
        repeating = {description for description, count in counts.items() if count >= knobs.min_occurrences}
        if not repeating:
            groups[f"{event_type}|credit|{category}|*"] = list(credits)
            continue
        for entry in credits:
            if entry.description in repeating:
                groups[f"{event_type}|credit|{category}|{entry.description}"].append(entry)
    return groups


def is_history(entry: LedgerEntry, as_of: date) -> bool:
    return (
        entry.treatment is CashTreatment.SETTLED
        and entry.lifecycle_role is LifecycleRole.STANDALONE
        and entry.amount_home is not None
        and entry.cash_date <= as_of
        and entry.event_type not in NON_RECURRING_TYPES
        and entry.direction is not Direction.NON_CASH
    )


def round_amount(value: Decimal, rounding: Rounding) -> Decimal:
    match rounding:
        case Rounding.NONE:
            return value
        case Rounding.CENT:
            return value.quantize(Decimal("0.01"), ROUND_HALF_UP)
        case Rounding.UNIT:
            return value.quantize(Decimal(1), ROUND_HALF_UP)
        case Rounding.HUNDRED:
            return (value / 100).quantize(Decimal(1), ROUND_HALF_UP) * 100


def estimate_amount(amounts: list[Decimal], knobs: EngineKnobs) -> tuple[Decimal, bool]:
    """Constant recent amounts are used as-is; variable ones use the configured conservative estimator."""
    recent = amounts[-knobs.cadence_window :]
    if len(set(recent)) == 1:
        return recent[-1], True
    match knobs.estimator:
        case Estimator.MEAN_LAST_3:
            value = sum(amounts[-3:]) / len(amounts[-3:])
        case Estimator.MEAN_LAST_6:
            value = sum(amounts[-6:]) / len(amounts[-6:])
        case Estimator.MEAN_ALL:
            value = sum(amounts) / len(amounts)
        case Estimator.MEDIAN_ALL:
            value = Decimal(median(amounts))
        case Estimator.MAX_LAST_3:
            value = max(amounts[-3:])
        case Estimator.MAX_LAST_6:
            value = max(amounts[-6:])
        case Estimator.MAX_ALL:
            value = max(amounts)
        case Estimator.LAST:
            value = amounts[-1]
    return round_amount(value, knobs.rounding), False


def detect_series(entries: Iterable[LedgerEntry], as_of: date, knobs: EngineKnobs) -> list[RecurringSeries]:
    groups = group_history(entries, as_of, knobs)
    low, high = knobs.monthly_cadence_range
    detected: list[RecurringSeries] = []
    for key, members in sorted(groups.items()):
        if len(members) < knobs.min_occurrences:
            continue
        members.sort(key=lambda entry: (entry.cash_date, entry.event_id))
        dates = [entry.cash_date for entry in members]
        gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:]) if later > earlier]
        if not gaps:
            continue
        cadence = int(median(gaps[-knobs.cadence_window :]))
        if cadence < 1 or (as_of - dates[-1]).days > knobs.stale_cadence_multiple * cadence:
            continue
        latest = members[-1]
        estimate, constant = estimate_amount([entry.amount_home for entry in members], knobs)
        anchor_day = Counter(day.day for day in dates[-knobs.cadence_window :]).most_common(1)[0][0]
        detected.append(
            RecurringSeries(
                key=key,
                event_type=latest.event_type,
                direction=latest.direction,
                category=latest.category,
                description=latest.description,
                flexibility=latest.flexibility,
                cadence_days=cadence,
                monthly=low <= cadence <= high,
                anchor_day=anchor_day,
                last_date=dates[-1],
                estimate=estimate,
                constant_amount=constant,
                occurrences=len(members),
                event_ids=tuple(entry.event_id for entry in members),
                minimum_allowed_amount=latest.minimum_allowed_amount_home,
            )
        )
    return detected
