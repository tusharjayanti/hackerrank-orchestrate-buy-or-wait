"""Optional spending changes: which flexible series may be stopped or reduced, and in what order to try them."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from itertools import combinations

from pydantic import BaseModel, ConfigDict

from ..formatting import format_plan_amount
from ..schemas.domain import Profile
from ..schemas.enums import Direction, Flexibility
from .forecast import CashFlow, FlowKind
from .recurrence import RecurringSeries

STOPPABLE = {Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE}
REDUCIBLE = {Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE}


class SpendingAction(StrEnum):
    STOP = "stop"
    REDUCE_TO = "reduce_to"


class SpendingChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SpendingAction
    event_id: str
    series_key: str
    category: str
    description: str
    new_amount: Decimal | None = None
    saving_per_occurrence: Decimal

    @property
    def token(self) -> str:
        if self.action is SpendingAction.STOP:
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{format_plan_amount(self.new_amount)}"


def eligible_changes(series: Sequence[RecurringSeries], profile: Profile) -> list[SpendingChange]:
    protected = set(profile.expense_categories_to_protect)
    may_stop = set(profile.expense_categories_user_is_willing_to_stop)
    may_reduce = set(profile.expense_categories_user_is_willing_to_reduce)
    changes: list[SpendingChange] = []
    for recurring in series:
        if recurring.direction is not Direction.DEBIT or recurring.category in protected:
            continue
        common = dict(event_id=recurring.latest_event_id, series_key=recurring.key, category=recurring.category, description=recurring.description)
        if recurring.flexibility in STOPPABLE and recurring.category in may_stop:
            changes.append(SpendingChange(action=SpendingAction.STOP, saving_per_occurrence=recurring.estimate, **common))
        floor = recurring.minimum_allowed_amount
        if (
            recurring.flexibility in REDUCIBLE
            and recurring.category in may_reduce
            and floor is not None
            and floor < recurring.estimate
        ):
            changes.append(
                SpendingChange(
                    action=SpendingAction.REDUCE_TO,
                    new_amount=floor,
                    saving_per_occurrence=recurring.estimate - floor,
                    **common,
                )
            )
    return changes


def change_combinations(
    changes: Sequence[SpendingChange], max_changes: int, flows: Sequence[CashFlow]
) -> list[tuple[SpendingChange, ...]]:
    """All combinations touching distinct series, smallest total horizon saving first, then fewest changes."""
    occurrences = Counter(flow.series_key for flow in flows if flow.kind is FlowKind.RECURRING)
    combos = [
        combo
        for size in range(1, max_changes + 1)
        for combo in combinations(changes, size)
        if len({change.series_key for change in combo}) == size
    ]

    def saving(combo: tuple[SpendingChange, ...]) -> Decimal:
        return sum((change.saving_per_occurrence * occurrences[change.series_key] for change in combo), Decimal(0))

    return sorted(combos, key=lambda combo: (saving(combo), len(combo), [change.token for change in combo]))


def apply_changes(flows: Sequence[CashFlow], changes: Sequence[SpendingChange]) -> list[CashFlow]:
    by_series = {change.series_key: change for change in changes}
    adjusted: list[CashFlow] = []
    for flow in flows:
        change = by_series.get(flow.series_key) if flow.kind is FlowKind.RECURRING else None
        if change is None:
            adjusted.append(flow)
        elif change.action is SpendingAction.REDUCE_TO:
            adjusted.append(flow.model_copy(update={"amount": -change.new_amount}))
    return adjusted
