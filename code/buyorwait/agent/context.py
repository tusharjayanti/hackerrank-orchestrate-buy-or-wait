"""Build the agent's context pack from engine results, with the grounding sets G5 checks against."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..evidence.validate import numbers_in
from ..formatting import format_long_date, format_money
from ..pipeline import RequestResult
from ..schemas.domain import PaymentOption, Profile
from ..schemas.enums import PaymentMethod
from ..schemas.evidence import AcceptedFact, EvidenceReview
from .scenarios import Scenario

MAX_EVALUATED_PLANS = 8


@dataclass
class AgentContext:
    request_id: str
    payload: dict[str, Any]
    scenario_summaries: dict[str, dict[str, Any]]
    allowed_numbers: dict[str, set[Decimal]]
    allowed_source_ids: set[str]
    required_phrases: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)

    def prompt_json(self) -> str:
        return json.dumps(self.payload, indent=1, sort_keys=True, default=str)


def summarize(result: RequestResult) -> dict[str, Any]:
    decision, row = result.decision, result.row
    currency = decision.currency.value
    lowest = decision.base_flows and min(
        (flow.day for flow in decision.base_flows), default=decision.request_date
    )
    summary: dict[str, Any] = {
        "amount_safe_to_pay_today": format_money(decision.amount_safe_to_pay, currency),
        "affordability_status": row.affordability_status.value,
        "recommended_payment_method": row.recommended_payment_method.value,
        "payment_plan": [
            {"date": format_long_date(payment.day), "amount": format_money(payment.amount, currency)}
            for payment in decision.payment_plan
        ]
        if row.payment_plan != "none"
        else [],
        "earliest_date_for_full_payment": format_long_date(decision.earliest_date_for_full_payment)
        if decision.earliest_date_for_full_payment
        else None,
        "spending_changes": [
            {
                "action": change.action.value,
                "event_id": change.event_id,
                "description": change.description,
                "new_amount": format_money(change.new_amount, currency) if change.new_amount is not None else None,
            }
            for change in (decision.spending_changes if row.spending_changes_needed != "none" else ())
        ],
        "lowest_projected_headroom_above_minimum": format_money(decision.base_min_headroom, currency),
        "evaluated_plans": [
            {
                "plan": evaluation.candidate.candidate_id.split("+")[0],
                "with_spending_changes": bool(evaluation.candidate.spending_changes),
                "safe": evaluation.safe,
                "completes_by_deadline": evaluation.completes_by_deadline,
            }
            for evaluation in decision.evaluations[:MAX_EVALUATED_PLANS]
        ],
        "options_not_considered": [{"option": item.candidate_id, "reason": item.reason} for item in decision.excluded],
        "draft_explanation": row.decision_explanation,
    }
    del lowest
    return summary


def _required_phrases(result: RequestResult) -> list[tuple[str, ...]]:
    decision, row = result.decision, result.row
    currency = decision.currency.value
    plan = decision.payment_plan
    method = row.recommended_payment_method
    phrases: list[tuple[str, ...]] = []
    if method is PaymentMethod.NOT_RECOMMENDED:
        phrases.append(("not", "cannot", "can't"))
    elif method is PaymentMethod.FULL_PAYMENT:
        phrases.append((format_money(decision.requested_amount, currency),))
    elif method is PaymentMethod.WAIT and decision.earliest_date_for_full_payment:
        phrases.append((format_long_date(decision.earliest_date_for_full_payment),))
    elif method is PaymentMethod.PARTIAL_PAYMENT and plan:
        phrases.append((format_money(plan[0].amount, currency),))
        phrases.append((format_long_date(plan[-1].day),))
    elif method is PaymentMethod.INSTALLMENTS and plan:
        phrases.append((format_money(plan[0].amount, currency),))
    if row.spending_changes_needed != "none":
        phrases.append(("stop", "reduce"))
    return phrases


def build_context(
    scenarios: Sequence[Scenario],
    results: Mapping[str, RequestResult],
    profile: Profile,
    options: Sequence[PaymentOption],
    facts: Sequence[AcceptedFact],
    reviews: Sequence[EvidenceReview],
) -> AgentContext:
    base = results[scenarios[0].scenario_id]
    request, decision = base.request, base.decision
    currency = profile.home_currency.value
    applied = set(decision.evidence.applied_fact_ids) if decision.evidence else set()
    evidence = [
        {
            "fact_id": fact.fact_id,
            "source_id": fact.source_id,
            "kind": fact.kind.value,
            "applied_to_forecast": fact.fact_id in applied or fact.related_event_id is not None,
            "amount": format_money(fact.amount_home, currency) if fact.amount_home is not None else None,
            "effective_date": format_long_date(fact.effective_date) if fact.effective_date else None,
            "quote": fact.quotes[0][:200] if fact.quotes else None,
        }
        for fact in facts
        if fact.sent_on is None or fact.sent_on <= request.request_date
    ]
    flags = [
        f"{review.source_id} contained instruction-like or scam text and was ignored"
        for review in reviews
        if review.injection_detected
    ]
    general = {
        "request": {
            "request_id": request.request_id,
            "request_date": format_long_date(request.request_date),
            "request_type": request.request_type.value,
            "requested_amount": format_money(request.requested_amount, currency),
            "desired_completion_date": format_long_date(request.desired_completion_date),
            "allows_partial_payment": request.allows_partial_payment,
            "customer_question_untrusted": request.request_text,
        },
        "customer": {
            "home_currency": currency,
            "current_available_balance": format_money(profile.current_available_balance, currency),
            "minimum_balance_to_keep": format_money(profile.minimum_balance_to_keep, currency),
            "payment_methods_user_will_consider": [method.value for method in profile.payment_methods_user_will_consider],
            "max_installment_months": profile.max_installment_months,
            "protected_categories": profile.expense_categories_to_protect,
        },
        "payment_options": [
            {
                "payment_option_id": option.payment_option_id,
                "method": option.payment_method.value,
                "payment_amount": format_money(option.payment_amount, currency),
                "number_of_payments": option.number_of_payments,
                "first_payment_date": format_long_date(option.first_payment_date),
                "total_payable": format_money(option.total_payable_amount, currency),
            }
            for option in options
        ],
        "evidence": evidence,
        "evidence_flags": flags,
    }
    summaries = {scenario.scenario_id: summarize(results[scenario.scenario_id]) for scenario in scenarios}
    payload = {
        **general,
        "scenarios": [{"scenario_id": scenario.scenario_id, "description": scenario.description} for scenario in scenarios],
        "base_scenario_result": summaries[scenarios[0].scenario_id],
    }
    general_numbers = numbers_in(json.dumps(general, default=str)) | {Decimal(90)}
    allowed_numbers = {
        scenario_id: general_numbers | numbers_in(json.dumps(summary, default=str)) for scenario_id, summary in summaries.items()
    }
    source_ids = {request.request_id, request.user_id, "engine", "customer"}
    source_ids |= {option.payment_option_id for option in options}
    source_ids |= {fact.fact_id for fact in facts} | {fact.source_id for fact in facts}
    source_ids |= {fact.related_event_id for fact in facts if fact.related_event_id}
    for result in results.values():
        source_ids |= {flow.event_id for flow in result.decision.base_flows if flow.event_id}
        source_ids |= {event_id for series in result.decision.series for event_id in series.event_ids[-3:]}
        source_ids |= {change.event_id for change in result.decision.spending_changes}
    return AgentContext(
        request_id=request.request_id,
        payload=payload,
        scenario_summaries=summaries,
        allowed_numbers=allowed_numbers,
        allowed_source_ids=source_ids,
        required_phrases={scenario.scenario_id: _required_phrases(results[scenario.scenario_id]) for scenario in scenarios},
    )
