"""Candidate payment plans, their safety evaluation, and the ranking rule from the problem statement."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ..schemas.domain import PaymentOption, Profile, PurchaseRequest
from ..schemas.enums import PaymentMethod
from .forecast import CashFlow, FlowKind, Timeline
from .spending import SpendingChange


class PlanKind(StrEnum):
    FULL_NOW = "full_now"
    PARTIAL = "partial"
    INSTALLMENTS = "installments"
    WAIT = "wait"


class ScheduledPayment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    amount: Decimal


class CandidatePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    kind: PlanKind
    method: PaymentMethod
    payments: tuple[ScheduledPayment, ...]
    payment_option_id: str | None = None
    spending_changes: tuple[SpendingChange, ...] = ()

    @property
    def total_paid(self) -> Decimal:
        return sum((payment.amount for payment in self.payments), Decimal(0))

    @property
    def first_day(self) -> date:
        return self.payments[0].day

    @property
    def last_day(self) -> date:
        return self.payments[-1].day


class ExcludedCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    reason: str


class PlanEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: CandidatePlan
    safe: bool
    completes_by_deadline: bool
    min_headroom: Decimal
    lowest_day: date
    first_breach_day: date | None

    def rank_key(self) -> tuple:
        plan = self.candidate
        return (
            not self.completes_by_deadline,
            bool(plan.spending_changes),
            plan.total_paid,
            plan.first_day,
            len(plan.payments),
            option_number(plan.payment_option_id),
        )


def option_number(payment_option_id: str | None) -> int:
    match = re.search(r"(\d+)$", payment_option_id or "")
    return int(match.group(1)) if match else 10**9


def build_candidates(
    request: PurchaseRequest,
    profile: Profile,
    options: Sequence[PaymentOption],
    safe_today: Decimal,
    earliest_full_day: date | None,
) -> tuple[list[CandidatePlan], list[ExcludedCandidate]]:
    accepted = set(profile.payment_methods_user_will_consider)
    amount = request.requested_amount
    today = request.request_date
    candidates: list[CandidatePlan] = []
    excluded: list[ExcludedCandidate] = []

    def exclude(candidate_id: str, reason: str) -> None:
        excluded.append(ExcludedCandidate(candidate_id=candidate_id, reason=reason))

    if PaymentMethod.FULL_PAYMENT in accepted:
        candidates.append(
            CandidatePlan(
                candidate_id="full_now",
                kind=PlanKind.FULL_NOW,
                method=PaymentMethod.FULL_PAYMENT,
                payments=(ScheduledPayment(day=today, amount=amount),),
            )
        )
        if earliest_full_day is not None and earliest_full_day > today:
            candidates.append(
                CandidatePlan(
                    candidate_id="wait",
                    kind=PlanKind.WAIT,
                    method=PaymentMethod.WAIT,
                    payments=(ScheduledPayment(day=earliest_full_day, amount=amount),),
                )
            )
        else:
            exclude("wait", "full payment never becomes safe later within the horizon" if earliest_full_day is None else "full payment is already safe today")
    else:
        exclude("full_now", "user does not consider full_payment")
        exclude("wait", "user does not consider full_payment")

    if PaymentMethod.PARTIAL_PAYMENT not in accepted:
        exclude("partial", "user does not consider partial_payment")
    elif not request.allows_partial_payment:
        exclude("partial", "request does not allow partial payment")
    elif not Decimal(0) < safe_today < amount:
        exclude("partial", "amount_safe_to_pay is not strictly between 0 and requested_amount")
    elif earliest_full_day is None or earliest_full_day > request.desired_completion_date:
        exclude("partial", "remaining balance cannot be paid safely by desired_completion_date")
    else:
        candidates.append(
            CandidatePlan(
                candidate_id="partial",
                kind=PlanKind.PARTIAL,
                method=PaymentMethod.PARTIAL_PAYMENT,
                payments=(
                    ScheduledPayment(day=today, amount=safe_today),
                    ScheduledPayment(day=earliest_full_day, amount=amount - safe_today),
                ),
            )
        )

    for option in options:
        if option.payment_method is not PaymentMethod.INSTALLMENTS:
            continue
        if PaymentMethod.INSTALLMENTS not in accepted:
            exclude(option.payment_option_id, "user does not consider installments")
        elif profile.max_installment_months is None:
            exclude(option.payment_option_id, "user has no max_installment_months")
        elif option.number_of_payments > profile.max_installment_months:
            exclude(option.payment_option_id, f"{option.number_of_payments} payments exceed max_installment_months {profile.max_installment_months}")
        else:
            candidates.append(
                CandidatePlan(
                    candidate_id=option.payment_option_id,
                    kind=PlanKind.INSTALLMENTS,
                    method=PaymentMethod.INSTALLMENTS,
                    payments=tuple(ScheduledPayment(day=day, amount=value) for day, value in option.schedule()),
                    payment_option_id=option.payment_option_id,
                )
            )
    return candidates, excluded


def evaluate(candidate: CandidatePlan, timeline: Timeline, request: PurchaseRequest) -> PlanEvaluation:
    payments = [
        CashFlow(day=payment.day, amount=-payment.amount, kind=FlowKind.PAYMENT, label=candidate.candidate_id)
        for payment in candidate.payments
    ]
    result = timeline.simulate(payments)
    return PlanEvaluation(
        candidate=candidate,
        safe=result.safe,
        completes_by_deadline=candidate.last_day <= request.desired_completion_date,
        min_headroom=result.min_headroom,
        lowest_day=result.lowest_day,
        first_breach_day=result.first_breach_day,
    )
