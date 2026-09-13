"""Ambiguities the engine cannot settle on its own, expressed as alternative knob settings to simulate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from ..engine.knobs import EngineKnobs
from ..schemas.domain import LedgerEntry, PurchaseRequest
from ..schemas.enums import CashTreatment

BASE_SCENARIO = "base"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    knobs: EngineKnobs


def detect_scenarios(entries: Sequence[LedgerEntry], request: PurchaseRequest, knobs: EngineKnobs) -> list[Scenario]:
    scenarios = [
        Scenario(
            BASE_SCENARIO,
            "Default reading: pending charges that mirror an already-settled charge are treated as duplicate records and ignored.",
            knobs,
        )
    ]
    end = request.request_date + timedelta(days=knobs.horizon_days)
    duplicates = [
        entry.event_id
        for entry in entries
        if entry.treatment is CashTreatment.DUPLICATE_SUSPECT and request.request_date <= entry.cash_date <= end
    ]
    if duplicates:
        scenarios.append(
            Scenario(
                "reserve_possible_duplicates",
                f"Safer reading: possible duplicate charge(s) {', '.join(duplicates)} are reserved as real debits "
                "because no reversal has been confirmed.",
                knobs.model_copy(update={"reserve_duplicate_suspects": not knobs.reserve_duplicate_suspects}),
            )
        )
    return scenarios
