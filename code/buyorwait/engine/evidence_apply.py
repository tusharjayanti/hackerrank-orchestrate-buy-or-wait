"""Apply resolved evidence adjustments to projected cash flows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from ..schemas.evidence import EvidenceAdjustments, IncomeAction, IncomeAdjustment, IncomeSource
from .forecast import CashFlow, FlowKind
from .recurrence import add_months

MATCH_WINDOW_DAYS = 7
MOVE_WINDOW_DAYS = 20
SOURCE_KEYWORDS: dict[IncomeSource, tuple[str, ...]] = {
    IncomeSource.PLATFORM_PAYOUT: ("payout", "earnings", "platform"),
    IncomeSource.FREELANCE_INVOICE: ("invoice", "project", "contract", "milestone", "retainer", "independent", "freelance", "consulting"),
    IncomeSource.COMMISSION: ("commission",),
    IncomeSource.BONUS: ("bonus",),
}
POOLED_SOURCES = {IncomeSource.PLATFORM_PAYOUT, IncomeSource.FREELANCE_INVOICE}


def _is_income(flow: CashFlow) -> bool:
    return flow.amount > 0 and flow.kind in (FlowKind.RECURRING, FlowKind.SCHEDULED_CREDIT)


def _is_recurring_income(flow: CashFlow) -> bool:
    return flow.amount > 0 and flow.kind is FlowKind.RECURRING


def _matches_source(flow: CashFlow, source: IncomeSource | None) -> bool:
    if source is None:
        return False
    label = flow.label.lower()
    if any(word in label for word in SOURCE_KEYWORDS.get(source, ())):
        return True
    return source in POOLED_SOURCES and bool(flow.series_key) and flow.series_key.endswith("|*")


def _apply_income(flows: list[CashFlow], adjustment: IncomeAdjustment, start: date, end: date) -> list[CashFlow]:
    label = f"evidence {adjustment.fact_id}"
    match adjustment.action:
        case IncomeAction.SET_AMOUNT_FROM:
            targets = [index for index, flow in enumerate(flows) if _is_income(flow) and flow.day >= adjustment.day]
            if not targets:
                return _apply_income(flows, adjustment.model_copy(update={"action": IncomeAction.CONFIRMED_MONTHLY}), start, end)
            for index in targets:
                flows[index] = flows[index].model_copy(update={"amount": adjustment.amount})
        case IncomeAction.SET_NEXT_AMOUNT:
            upcoming = sorted((flow.day, index) for index, flow in enumerate(flows) if _is_income(flow) and flow.day >= max(start, adjustment.day))
            if upcoming:
                index = upcoming[0][1]
                flows[index] = flows[index].model_copy(update={"amount": adjustment.amount})
        case IncomeAction.ADD_TO_NEXT:
            upcoming = sorted((flow.day, index) for index, flow in enumerate(flows) if _is_income(flow) and flow.day >= max(start, adjustment.day))
            if upcoming:
                index = upcoming[0][1]
                flows[index] = flows[index].model_copy(update={"amount": flows[index].amount + adjustment.amount})
        case IncomeAction.MOVE_NEXT_DATE:
            if start <= adjustment.day <= end:
                nearest = sorted(
                    (abs((flow.day - adjustment.day).days), index)
                    for index, flow in enumerate(flows)
                    if _is_income(flow) and abs((flow.day - adjustment.day).days) <= MOVE_WINDOW_DAYS
                )
                if nearest:
                    index = nearest[0][1]
                    flows[index] = flows[index].model_copy(update={"day": adjustment.day})
        case IncomeAction.CONFIRMED_MONTHLY:
            step, day = 0, adjustment.day
            while day <= end:
                if day >= start:
                    near = [index for index, flow in enumerate(flows) if _is_income(flow) and abs((flow.day - day).days) <= MATCH_WINDOW_DAYS]
                    if near:
                        flows[near[0]] = flows[near[0]].model_copy(update={"amount": adjustment.amount, "day": day})
                        flows = [flow for index, flow in enumerate(flows) if index not in near[1:]]
                    else:
                        flows.append(CashFlow(day=day, amount=adjustment.amount, kind=FlowKind.RECURRING, label=label, series_key=f"evidence|{adjustment.fact_id}"))
                step += 1
                day = add_months(adjustment.day, step)
        case IncomeAction.END_ALL:
            flows = [flow for flow in flows if not _is_recurring_income(flow)]
        case IncomeAction.END_MATCHING:
            flows = [flow for flow in flows if not (_is_recurring_income(flow) and _matches_source(flow, adjustment.source))]
        case IncomeAction.REPLACE_TOTAL:
            by_series: dict[str, list[CashFlow]] = defaultdict(list)
            for flow in flows:
                if _is_recurring_income(flow):
                    by_series[flow.series_key or ""].append(flow)
            if by_series:
                keep = max(by_series, key=lambda key: (len(by_series[key]), max(flow.amount for flow in by_series[key]), key))
                flows = [
                    flow.model_copy(update={"amount": adjustment.amount}) if _is_recurring_income(flow) else flow
                    for flow in flows
                    if not _is_recurring_income(flow) or (flow.series_key or "") == keep
                ]
    return flows


def apply_evidence(flows: Sequence[CashFlow], adjustments: EvidenceAdjustments, start: date, end: date) -> list[CashFlow]:
    adjusted = list(flows)
    for adjustment in adjustments.income:
        adjusted = _apply_income(adjusted, adjustment, start, end)
    for scale in adjustments.expense_scales:
        adjusted = [
            flow.model_copy(update={"amount": flow.amount * scale.factor})
            if flow.kind is FlowKind.RECURRING and flow.amount < 0 and flow.label == scale.category and flow.day >= scale.from_day
            else flow
            for flow in adjusted
        ]
    for credit in adjustments.one_time_credits:
        if start <= credit.day <= end:
            adjusted.append(CashFlow(day=credit.day, amount=credit.amount, kind=FlowKind.SCHEDULED_CREDIT, label=credit.label))
    return adjusted
