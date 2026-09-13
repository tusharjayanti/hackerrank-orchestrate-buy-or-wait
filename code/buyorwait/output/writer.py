"""The submission row model and CSV writer (exact column order)."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..engine.decide import EngineDecision
from ..formatting import format_plan_amount, format_safe_amount
from ..schemas.enums import AffordabilityStatus, PaymentMethod

OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)


class OutputRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    amount_safe_to_pay: str
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str

    @classmethod
    def from_decision(cls, decision: EngineDecision, explanation: str) -> OutputRow:
        plan = "|".join(f"{payment.day.isoformat()}:{format_plan_amount(payment.amount)}" for payment in decision.payment_plan)
        changes = "|".join(change.token for change in decision.spending_changes)
        earliest = decision.earliest_date_for_full_payment
        return cls(
            request_id=decision.request_id,
            amount_safe_to_pay=format_safe_amount(decision.amount_safe_to_pay),
            affordability_status=decision.affordability_status,
            recommended_payment_method=decision.recommended_payment_method,
            payment_plan=plan or "none",
            earliest_date_for_full_payment=earliest.isoformat() if earliest else "",
            spending_changes_needed=changes or "none",
            decision_explanation=explanation,
        )

    def as_csv_dict(self) -> dict[str, str]:
        return {column: str(getattr(self, column)) for column in OUTPUT_COLUMNS}


def write_output(rows: Iterable[OutputRow], path: Path, request_order: Sequence[str]) -> None:
    by_id = {row.request_id: row for row in rows}
    missing = [request_id for request_id in request_order if request_id not in by_id]
    if missing:
        raise ValueError(f"missing output rows for {len(missing)} requests, e.g. {missing[:3]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for request_id in request_order:
            writer.writerow(by_id[request_id].as_csv_dict())
