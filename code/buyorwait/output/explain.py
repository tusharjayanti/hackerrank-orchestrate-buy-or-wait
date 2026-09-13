"""Template explanations grounded in engine values, phrased like the sample outputs."""

from __future__ import annotations

from collections.abc import Sequence

from ..engine.decide import EngineDecision
from ..engine.plans import PlanKind
from ..engine.spending import SpendingAction, SpendingChange
from ..formatting import format_long_date, format_money
from ..schemas.enums import AffordabilityStatus


def describe_changes(changes: Sequence[SpendingChange], currency: str) -> str:
    phrases = [
        f"stop the {change.description.lower()}"
        if change.action is SpendingAction.STOP
        else f"reduce the {change.description.lower()} to {format_money(change.new_amount, currency)}"
        for change in changes
    ]
    joined = phrases[0] if len(phrases) == 1 else f"{', '.join(phrases[:-1])} and {phrases[-1]}"
    return joined[0].upper() + joined[1:]


def explain(decision: EngineDecision) -> str:
    currency = decision.currency.value
    total = format_money(decision.requested_amount, currency)
    minimum = format_money(decision.minimum_balance, currency)
    safe = format_money(decision.amount_safe_to_pay, currency)

    if decision.chosen is None:
        if decision.amount_safe_to_pay > 0 and decision.earliest_date_for_full_payment is None:
            return (
                f"Do not proceed with the {total} request. Although {safe} is available today, "
                f"the full amount cannot be completed safely within 90 days."
            )
        return (
            f"Do not make this payment by {format_long_date(decision.desired_completion_date)}. "
            f"None of the available options keeps the {minimum} minimum protected."
        )

    plan = decision.chosen.candidate
    if plan.kind is PlanKind.WAIT:
        return (
            f"Pay {total} in full on {format_long_date(plan.first_day)}. "
            f"Paying earlier would take the balance below the {minimum} minimum."
        )
    if plan.kind is PlanKind.PARTIAL:
        first, second = plan.payments
        return (
            f"Pay {format_money(first.amount, currency)} today and the remaining {format_money(second.amount, currency)} "
            f"on {format_long_date(second.day)}. This completes the full request and keeps the {minimum} minimum protected."
        )

    if plan.kind is PlanKind.INSTALLMENTS:
        action = (
            f"use {len(plan.payments)} installments of {format_money(plan.payments[0].amount, currency)}, "
            f"starting {format_long_date(plan.first_day)}"
        )
    else:
        action = f"pay {total} today"

    if plan.spending_changes:
        return f"{describe_changes(plan.spending_changes, currency)}, then {action}. This leaves at least {minimum} available."
    if decision.affordability_status is AffordabilityStatus.AFFORDABLE_NOW:
        return f"{action[0].upper()}{action[1:]}. This leaves at least {minimum} available over the next 90 days."
    return f"{action[0].upper()}{action[1:]}. This leaves at least {minimum} available."
