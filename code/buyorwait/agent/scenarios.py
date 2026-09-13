"""Ambiguities the engine cannot settle on its own, expressed as alternative knob settings to simulate.

The agent acts as the judge between these readings, applying the problem statement's conflict rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from ..engine.knobs import EngineKnobs, horizon_end
from ..schemas.domain import LedgerEntry, PurchaseRequest
from ..schemas.enums import CashTreatment
from ..schemas.evidence import AcceptedFact, EvidenceKind

BASE_SCENARIO = "base"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    knobs: EngineKnobs


def detect_scenarios(
    entries: Sequence[LedgerEntry], request: PurchaseRequest, knobs: EngineKnobs, facts: Sequence[AcceptedFact] = ()
) -> list[Scenario]:
    scenarios = [
        Scenario(
            BASE_SCENARIO,
            "Default reading: possible duplicate charges are ignored as duplicate records; reduced or temporary pay applies "
            "only to the payroll the message names; one-time adjustments without a stated date are not counted.",
            knobs,
        )
    ]
    end = horizon_end(request.request_date, knobs)
    duplicates = [
        entry.event_id
        for entry in entries
        if entry.treatment is CashTreatment.DUPLICATE_SUSPECT and request.request_date <= entry.cash_date <= end
    ]
    if duplicates:
        scenarios.append(
            Scenario(
                "reserve_possible_duplicates",
                f"Alternative reading: possible duplicate charge(s) {', '.join(duplicates)} are reserved as real debits "
                "because no reversal has been confirmed.",
                knobs.model_copy(update={"reserve_duplicate_suspects": not knobs.reserve_duplicate_suspects}),
            )
        )

    relevant = [fact for fact in facts if fact.sent_on is not None and fact.sent_on <= request.request_date]
    reduced_pay = [fact.fact_id for fact in relevant if fact.kind is EvidenceKind.SALARY_NEXT_PAYMENT_AMOUNT]
    if reduced_pay:
        scenarios.append(
            Scenario(
                "reduced_pay_continues",
                f"Alternative reading of {', '.join(reduced_pay)}: the stated pay applies to every later payroll in the "
                "forecast, not only the next one.",
                knobs.model_copy(update={"reduced_pay_continues": True}),
            )
        )
    undated = [
        fact.fact_id
        for fact in relevant
        if fact.kind is EvidenceKind.ONE_TIME_INCOME_CONFIRMED and fact.effective_date is None and fact.amount_home is not None
    ]
    if undated:
        scenarios.append(
            Scenario(
                "count_undated_one_time_income",
                f"Alternative reading of {', '.join(undated)}: the confirmed one-time amount is paid with the next salary "
                "and counted as income.",
                knobs.model_copy(update={"count_undated_one_time_income": True}),
            )
        )
    return scenarios
