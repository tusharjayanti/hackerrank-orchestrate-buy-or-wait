"""Cash-flow projection over the forecast horizon and safety checks for payments."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ..schemas.domain import LedgerEntry
from ..schemas.enums import CashTreatment, Direction
from .knobs import EngineKnobs, IntradayStep
from .recurrence import RecurringSeries, add_months


class FlowKind(StrEnum):
    RECURRING = "recurring"
    RESERVED_DEBIT = "reserved_debit"
    SCHEDULED_CREDIT = "scheduled_credit"
    PAYMENT = "payment"


class CashFlow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    amount: Decimal  # signed: credits positive, debits and payments negative
    kind: FlowKind
    label: str
    series_key: str | None = None
    event_id: str | None = None

    @property
    def step(self) -> IntradayStep:
        if self.kind is FlowKind.PAYMENT:
            return "payment"
        return "credit" if self.amount > 0 else "debit"


class SafetyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_headroom: Decimal
    lowest_day: date
    first_breach_day: date | None

    @property
    def safe(self) -> bool:
        return self.first_breach_day is None


def build_base_flows(
    entries: Iterable[LedgerEntry],
    series: Sequence[RecurringSeries],
    start: date,
    end: date,
    knobs: EngineKnobs,
) -> tuple[list[CashFlow], list[str]]:
    """Known pending/scheduled events plus projected recurring series inside [start, end]."""
    flows: list[CashFlow] = []
    notes: list[str] = []
    for entry in entries:
        if not start <= entry.cash_date <= end:
            continue
        reserve = entry.treatment is CashTreatment.RESERVED_DEBIT or (
            entry.treatment is CashTreatment.DUPLICATE_SUSPECT and knobs.reserve_duplicate_suspects
        )
        if not reserve and entry.treatment is not CashTreatment.SCHEDULED_CREDIT:
            continue
        if entry.amount_home is None:
            notes.append(f"{entry.event_id}: blank amount not yet resolved from evidence; excluded")
            continue
        if reserve:
            flows.append(
                CashFlow(
                    day=entry.cash_date,
                    amount=-entry.amount_home,
                    kind=FlowKind.RESERVED_DEBIT,
                    label=entry.description,
                    event_id=entry.event_id,
                )
            )
        else:
            flows.append(
                CashFlow(
                    day=entry.cash_date,
                    amount=entry.amount_home,
                    kind=FlowKind.SCHEDULED_CREDIT,
                    label=entry.description,
                    event_id=entry.event_id,
                )
            )

    scheduled_credit_days = [flow.day for flow in flows if flow.kind is FlowKind.SCHEDULED_CREDIT]
    if knobs.project_confirmed_credits_forward:
        # A confirmed salary with no detectable income series (e.g. a first job) is assumed to repeat monthly.
        income_categories = {recurring.category for recurring in series if recurring.direction is Direction.CREDIT}
        for entry in entries:
            if (
                entry.treatment is not CashTreatment.SCHEDULED_CREDIT
                or entry.amount_home is None
                or entry.category in income_categories
                or not start <= entry.cash_date <= end
            ):
                continue
            step = 1
            while (day := add_months(entry.cash_date, step)) <= end:
                flows.append(
                    CashFlow(
                        day=day,
                        amount=entry.amount_home,
                        kind=FlowKind.RECURRING,
                        label=f"{entry.description} (projected monthly)",
                        event_id=entry.event_id,
                    )
                )
                step += 1

    for recurring in series:
        for day in recurring.dates_between(start, end):
            if recurring.direction is Direction.CREDIT and any(
                abs((day - other).days) <= knobs.scheduled_credit_dedupe_days for other in scheduled_credit_days
            ):
                continue
            flows.append(
                CashFlow(
                    day=day,
                    amount=recurring.signed_estimate,
                    kind=FlowKind.RECURRING,
                    label=recurring.description if recurring.direction is Direction.CREDIT else recurring.category,
                    series_key=recurring.key,
                    event_id=recurring.latest_event_id,
                )
            )
    return flows, notes


class Timeline:
    """Balance path over [start, end] with O(log n) lump-payment capacity queries."""

    def __init__(
        self,
        opening_balance: Decimal,
        minimum_balance: Decimal,
        flows: Iterable[CashFlow],
        start: date,
        end: date,
        intraday_order: Sequence[IntradayStep],
    ) -> None:
        self.opening_balance = opening_balance
        self.minimum_balance = minimum_balance
        self.start = start
        self.end = end
        self._rank = {step: index for index, step in enumerate(intraday_order)}
        self.flows = sorted((flow for flow in flows if start <= flow.day <= end), key=self._sort_key)

        self._keys = [(flow.day, self._rank[flow.step]) for flow in self.flows]
        balances = [opening_balance]
        for flow in self.flows:
            balances.append(balances[-1] + flow.amount)
        self._prefix_min = balances[:]
        for index in range(1, len(balances)):
            self._prefix_min[index] = min(self._prefix_min[index - 1], balances[index])
        self._suffix_min = balances[:]
        for index in range(len(balances) - 2, -1, -1):
            self._suffix_min[index] = min(self._suffix_min[index + 1], balances[index])
        self._balances = balances

    def _sort_key(self, flow: CashFlow) -> tuple:
        return (flow.day, self._rank[flow.step], flow.amount, flow.label)

    def capacity_on(self, day: date) -> Decimal | None:
        """Largest single payment on `day` that keeps every later point above the minimum.

        None when the balance already breaches the minimum before the payment would happen.
        """
        position = bisect_left(self._keys, (day, self._rank["payment"]))
        if self._prefix_min[position] < self.minimum_balance:
            return None
        return self._suffix_min[position] - self.minimum_balance

    def earliest_day_for(self, amount: Decimal) -> date | None:
        day = self.start
        while day <= self.end:
            capacity = self.capacity_on(day)
            if capacity is not None and capacity >= amount:
                return day
            day += timedelta(days=1)
        return None

    def simulate(self, payments: Iterable[CashFlow] = ()) -> SafetyResult:
        merged = sorted(
            [*self.flows, *(payment for payment in payments if self.start <= payment.day <= self.end)],
            key=self._sort_key,
        )
        balance = self.opening_balance
        lowest, lowest_day, breach_day = balance, self.start, None
        if balance < self.minimum_balance:
            breach_day = self.start
        for flow in merged:
            balance += flow.amount
            if balance < lowest:
                lowest, lowest_day = balance, flow.day
            if breach_day is None and balance < self.minimum_balance:
                breach_day = flow.day
        return SafetyResult(min_headroom=lowest - self.minimum_balance, lowest_day=lowest_day, first_breach_day=breach_day)
