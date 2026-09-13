"""Engine entry point: forecast, safe amount, earliest date, plan choice and status mapping for one request."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from pydantic import BaseModel, ConfigDict

from ..formatting import CENT
from ..schemas.domain import LedgerEntry, PaymentOption, Profile, PurchaseRequest
from ..schemas.enums import AffordabilityStatus, Currency, PaymentMethod
from ..schemas.evidence import EvidenceAdjustments
from .forecast import CashFlow, Timeline, build_base_flows
from .knobs import EngineKnobs
from .plans import (
    CandidatePlan,
    ExcludedCandidate,
    PlanEvaluation,
    PlanKind,
    ScheduledPayment,
    build_candidates,
    evaluate,
)
from .evidence_apply import apply_evidence
from .recurrence import RecurringSeries, detect_series
from .spending import SpendingChange, apply_changes, change_combinations, eligible_changes


class EngineDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    user_id: str
    currency: Currency
    request_date: date
    requested_amount: Decimal
    desired_completion_date: date
    minimum_balance: Decimal
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    chosen: PlanEvaluation | None
    evaluations: list[PlanEvaluation]
    excluded: list[ExcludedCandidate]
    series: list[RecurringSeries]
    base_flows: list[CashFlow]
    base_min_headroom: Decimal
    notes: list[str]
    evidence: EvidenceAdjustments | None = None

    @property
    def payment_plan(self) -> tuple[ScheduledPayment, ...]:
        return self.chosen.candidate.payments if self.chosen else ()

    @property
    def spending_changes(self) -> tuple[SpendingChange, ...]:
        return self.chosen.candidate.spending_changes if self.chosen else ()


def _best(evaluations: Sequence[PlanEvaluation], knobs: EngineKnobs) -> PlanEvaluation | None:
    acceptable = [
        evaluation
        for evaluation in evaluations
        if evaluation.safe and (evaluation.completes_by_deadline or not knobs.require_deadline)
    ]
    return min(acceptable, key=PlanEvaluation.rank_key, default=None)


def _search_spending_changes(
    candidates: Sequence[CandidatePlan],
    series: Sequence[RecurringSeries],
    base_flows: Sequence[CashFlow],
    profile: Profile,
    request: PurchaseRequest,
    start: date,
    end: date,
    knobs: EngineKnobs,
) -> tuple[PlanEvaluation | None, list[PlanEvaluation]]:
    """Try spending-change sets from the smallest total saving upward; the first set that makes a plan safe wins."""
    changeable = [candidate for candidate in candidates if candidate.kind in (PlanKind.FULL_NOW, PlanKind.INSTALLMENTS)]
    if not changeable:
        return None, []
    tried: list[PlanEvaluation] = []
    for combo in change_combinations(eligible_changes(series, profile), knobs.max_spending_changes, base_flows):
        timeline = Timeline(
            profile.current_available_balance,
            profile.minimum_balance_to_keep,
            apply_changes(base_flows, combo),
            start,
            end,
            knobs.intraday_order,
        )
        tokens = "|".join(change.token for change in combo)
        evaluations = [
            evaluate(
                candidate.model_copy(update={"spending_changes": combo, "candidate_id": f"{candidate.candidate_id}+{tokens}"}),
                timeline,
                request,
            )
            for candidate in changeable
        ]
        best = _best(evaluations, knobs)
        if best is not None:
            tried.extend(evaluations)
            return best, tried
    return None, tried


def _status_for(chosen: PlanEvaluation | None) -> tuple[AffordabilityStatus, PaymentMethod]:
    if chosen is None:
        return AffordabilityStatus.NOT_AFFORDABLE, PaymentMethod.NOT_RECOMMENDED
    plan = chosen.candidate
    if plan.kind is PlanKind.WAIT:
        return AffordabilityStatus.AFFORDABLE_LATER, PaymentMethod.WAIT
    if plan.spending_changes or plan.kind in (PlanKind.PARTIAL, PlanKind.INSTALLMENTS):
        return AffordabilityStatus.AFFORDABLE_WITH_PLAN, plan.method
    return AffordabilityStatus.AFFORDABLE_NOW, PaymentMethod.FULL_PAYMENT


def decide(
    request: PurchaseRequest,
    profile: Profile,
    options: Sequence[PaymentOption],
    entries: Sequence[LedgerEntry],
    knobs: EngineKnobs,
    adjustments: EvidenceAdjustments | None = None,
) -> EngineDecision:
    start = request.request_date
    end = start + timedelta(days=knobs.horizon_days)
    amount = request.requested_amount

    excluded = frozenset(adjustments.excluded_history_event_ids) if adjustments else frozenset()
    series = detect_series(entries, start, knobs, excluded)
    base_flows, notes = build_base_flows(entries, series, start, end, knobs)
    if adjustments is not None:
        base_flows = apply_evidence(base_flows, adjustments, start, end)
    timeline = Timeline(
        profile.current_available_balance, profile.minimum_balance_to_keep, base_flows, start, end, knobs.intraday_order
    )

    capacity = timeline.capacity_on(start) or Decimal(0)
    safe_today = min(amount, max(Decimal(0), capacity)).quantize(CENT, ROUND_FLOOR)
    earliest = timeline.earliest_day_for(amount)

    candidates, excluded = build_candidates(request, profile, options, safe_today, earliest)
    evaluations = [evaluate(candidate, timeline, request) for candidate in candidates]
    chosen = _best(evaluations, knobs)
    if chosen is None:
        chosen, change_evaluations = _search_spending_changes(
            candidates, series, base_flows, profile, request, start, end, knobs
        )
        evaluations.extend(change_evaluations)

    status, method = _status_for(chosen)
    return EngineDecision(
        request_id=request.request_id,
        user_id=request.user_id,
        currency=profile.home_currency,
        request_date=start,
        requested_amount=amount,
        desired_completion_date=request.desired_completion_date,
        minimum_balance=profile.minimum_balance_to_keep,
        amount_safe_to_pay=safe_today,
        earliest_date_for_full_payment=earliest,
        affordability_status=status,
        recommended_payment_method=method,
        chosen=chosen,
        evaluations=evaluations,
        excluded=excluded,
        series=series,
        base_flows=timeline.flows,
        base_min_headroom=timeline.simulate().min_headroom,
        notes=notes,
        evidence=adjustments,
    )
