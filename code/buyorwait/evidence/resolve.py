"""Turn accepted evidence facts into deterministic forecast adjustments for one request."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from ..engine.knobs import EngineKnobs
from ..schemas.domain import LedgerEntry, PurchaseRequest
from ..schemas.enums import CashTreatment, Direction
from ..schemas.evidence import (
    AcceptedFact,
    EvidenceAdjustments,
    EvidenceKind,
    ExpenseScale,
    IncomeAction,
    IncomeAdjustment,
    IncomeSource,
    OneTimeCredit,
)

K = EvidenceKind
MATCHABLE_SOURCES = {IncomeSource.PLATFORM_PAYOUT, IncomeSource.FREELANCE_INVOICE, IncomeSource.COMMISSION, IncomeSource.BONUS}
TRANSFER_LOOKBACK_DAYS = 45


def _internal_transfer_ids(entries: Sequence[LedgerEntry], sent_on: date) -> list[str]:
    window = [
        entry
        for entry in entries
        if entry.treatment is CashTreatment.SETTLED
        and entry.amount_home is not None
        and sent_on - timedelta(days=TRANSFER_LOOKBACK_DAYS) <= entry.cash_date <= sent_on
    ]
    debits = [entry for entry in window if entry.direction is Direction.DEBIT]
    ids: list[str] = []
    for credit in (entry for entry in window if entry.direction is Direction.CREDIT):
        match = next(
            (debit for debit in debits if debit.cash_date == credit.cash_date and debit.amount_home == credit.amount_home),
            None,
        )
        if match is not None:
            ids += [match.event_id, credit.event_id]
    return ids


def resolve_adjustments(
    facts: Sequence[AcceptedFact],
    request: PurchaseRequest,
    entries: Sequence[LedgerEntry],
    knobs: EngineKnobs | None = None,
) -> EvidenceAdjustments:
    """Facts sent on or before the request date, applied oldest first so newer records from a source win."""
    knobs = knobs or EngineKnobs()
    adjustments = EvidenceAdjustments()
    relevant = sorted(
        (fact for fact in facts if fact.sent_on is not None and fact.sent_on <= request.request_date),
        key=lambda fact: (fact.sent_on, fact.fact_id),
    )
    # "One income ended; the remaining confirmed salary is X" is a single amendment, not an end to all income.
    remaining_total_sources = {fact.source_id for fact in relevant if fact.kind is K.INCOME_REMAINING_TOTAL}
    for fact in relevant:
        day = fact.effective_date or fact.sent_on
        amount = fact.amount_home
        applied = True
        match fact.kind:
            case K.SALARY_AMOUNT_CHANGE:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.SET_AMOUNT_FROM, amount=amount, day=day, fact_id=fact.fact_id))
            case K.SALARY_NEXT_PAYMENT_AMOUNT:
                action = IncomeAction.SET_AMOUNT_FROM if knobs.reduced_pay_continues else IncomeAction.SET_NEXT_AMOUNT
                adjustments.income.append(IncomeAdjustment(action=action, amount=amount, day=fact.sent_on, fact_id=fact.fact_id))
            case K.SALARY_DATE_CHANGE:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.MOVE_NEXT_DATE, day=fact.effective_date, fact_id=fact.fact_id))
            case K.SALARY_CONFIRMED:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.CONFIRMED_MONTHLY, amount=amount, day=fact.effective_date, fact_id=fact.fact_id))
            case K.INCOME_ENDED if fact.source_id in remaining_total_sources:
                applied = False
                adjustments.notes.append(f"{fact.fact_id}: superseded by the remaining total stated in {fact.source_id}")
            case K.INCOME_ENDED:
                if fact.income_source in MATCHABLE_SOURCES:
                    adjustments.income.append(IncomeAdjustment(action=IncomeAction.END_MATCHING, source=fact.income_source, fact_id=fact.fact_id))
                else:
                    adjustments.income.append(IncomeAdjustment(action=IncomeAction.END_ALL, fact_id=fact.fact_id))
            case K.INCOME_REMAINING_TOTAL:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.REPLACE_TOTAL, amount=amount, fact_id=fact.fact_id))
            case K.INCOME_NOT_CONFIRMED if fact.income_source in MATCHABLE_SOURCES:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.END_MATCHING, source=fact.income_source, fact_id=fact.fact_id))
            case K.ONE_TIME_INCOME_CONFIRMED if fact.effective_date is None and amount is not None and knobs.count_undated_one_time_income:
                adjustments.income.append(IncomeAdjustment(action=IncomeAction.ADD_TO_NEXT, amount=amount, day=fact.sent_on, fact_id=fact.fact_id))
            case K.ONE_TIME_INCOME_CONFIRMED if fact.effective_date is not None and amount is not None:
                adjustments.one_time_credits.append(
                    OneTimeCredit(day=fact.effective_date, amount=amount, label=f"confirmed {fact.income_source}", fact_id=fact.fact_id)
                )
                if fact.income_source in MATCHABLE_SOURCES:
                    adjustments.income.append(IncomeAdjustment(action=IncomeAction.END_MATCHING, source=fact.income_source, fact_id=fact.fact_id))
            case K.EXPENSE_PERCENT_CHANGE if fact.percent is not None and fact.category:
                adjustments.expense_scales.append(
                    ExpenseScale(category=fact.category.lower(), factor=Decimal(1) + fact.percent / 100, from_day=fact.sent_on, fact_id=fact.fact_id)
                )
            case K.INTERNAL_TRANSFER:
                adjustments.excluded_history_event_ids += _internal_transfer_ids(entries, fact.sent_on)
            case _:
                applied = False
                adjustments.notes.append(f"{fact.fact_id}: {fact.kind} has no forecast effect")
        if applied:
            adjustments.applied_fact_ids.append(fact.fact_id)
    return adjustments
