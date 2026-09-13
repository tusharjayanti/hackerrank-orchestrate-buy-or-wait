"""S3b metamorphic relations: label-free checks that outputs move in the right direction when inputs change."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from ..ingest.loaders import Dataset
from ..output.writer import OutputRow
from ..pipeline import EnginePipeline
from ..schemas.domain import FinancialEvent
from ..schemas.enums import AffordabilityStatus

TOLERANCE = Decimal("0.01")
STATUS_RANK = {
    AffordabilityStatus.AFFORDABLE_NOW: 0,
    AffordabilityStatus.AFFORDABLE_WITH_PLAN: 1,
    AffordabilityStatus.AFFORDABLE_LATER: 2,
    AffordabilityStatus.NOT_AFFORDABLE: 3,
}


class RelationViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    detail: str


class RelationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation: str
    description: str
    checked: int
    violations: list[RelationViolation] = Field(default_factory=list)
    soft_status_regressions: list[RelationViolation] = Field(default_factory=list)


class MetamorphicReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    relations: list[RelationResult]


def _safe(row: OutputRow) -> Decimal:
    return Decimal(row.amount_safe_to_pay)


def _earliest(row: OutputRow) -> date:
    return date.fromisoformat(row.earliest_date_for_full_payment) if row.earliest_date_for_full_payment else date.max


def _run_rows(dataset: Dataset) -> dict[str, OutputRow]:
    pipeline = EnginePipeline(dataset)
    return {request.request_id: pipeline.run_request(request).row for request in dataset.all_requests}


def _scaled_profiles(dataset: Dataset, field: str, factor: Decimal) -> Dataset:
    profiles = {
        user_id: profile.model_copy(update={field: (getattr(profile, field) * factor).quantize(TOLERANCE, ROUND_HALF_UP)})
        for user_id, profile in dataset.profiles.items()
    }
    return dataclasses.replace(dataset, profiles=profiles)


def _added_events(dataset: Dataset, kind: str) -> Dataset:
    extra: list[FinancialEvent] = []
    for request in dataset.all_requests:
        profile = dataset.profiles[request.user_id]
        amount = (profile.current_available_balance * Decimal("0.05")).quantize(TOLERANCE, ROUND_HALF_UP)
        settle = request.request_date + timedelta(days=3)
        values = {
            "pending_debit": ("expense", "debit", "pending"),
            "pending_credit": ("refund", "credit", "pending"),
            "cancelled_debit": ("expense", "debit", "cancelled"),
        }[kind]
        extra.append(
            FinancialEvent.model_validate(
                {
                    "event_id": f"metamorphic_{kind}_{request.request_id}",
                    "user_id": request.user_id,
                    "event_type": values[0],
                    "description": f"Metamorphic {kind.replace('_', ' ')}",
                    "category": "shopping",
                    "direction": values[1],
                    "amount": str(amount),
                    "currency": profile.home_currency.value,
                    "event_date": request.request_date.isoformat(),
                    "settlement_date": settle.isoformat(),
                    "status": values[2],
                    "linked_event_id": "",
                    "flexibility": "fixed",
                    "minimum_allowed_amount": "",
                }
            )
        )
    return dataclasses.replace(dataset, events=[*dataset.events, *extra])


def _scaled_requests(dataset: Dataset, factor: Decimal) -> Dataset:
    def scale(requests):
        return [
            request.model_copy(update={"requested_amount": (request.requested_amount * factor).quantize(TOLERANCE, ROUND_HALF_UP)})
            for request in requests
        ]

    return dataclasses.replace(dataset, requests=scale(dataset.requests), sample_requests=scale(dataset.sample_requests))


Check = Callable[[OutputRow, OutputRow, Dataset], str | None]


def _monotone(safe_direction: int, earliest_direction: int) -> Check:
    """safe_direction +1: new safe >= base; -1: new safe <= base. earliest likewise (+1 later-or-equal, -1 earlier-or-equal)."""

    def check(base: OutputRow, new: OutputRow, dataset: Dataset) -> str | None:
        problems = []
        if safe_direction > 0 and _safe(new) + TOLERANCE < _safe(base):
            problems.append(f"safe fell {base.amount_safe_to_pay} -> {new.amount_safe_to_pay}")
        if safe_direction < 0 and _safe(new) > _safe(base) + TOLERANCE:
            problems.append(f"safe rose {base.amount_safe_to_pay} -> {new.amount_safe_to_pay}")
        if earliest_direction > 0 and _earliest(new) < _earliest(base):
            problems.append(f"earliest moved earlier {base.earliest_date_for_full_payment or '-'} -> {new.earliest_date_for_full_payment or '-'}")
        if earliest_direction < 0 and _earliest(new) > _earliest(base):
            problems.append(f"earliest moved later {base.earliest_date_for_full_payment or '-'} -> {new.earliest_date_for_full_payment or '-'}")
        return "; ".join(problems) or None

    return check


def _amount_lowered(base: OutputRow, new: OutputRow, dataset: Dataset) -> str | None:
    request = next(r for r in dataset.all_requests if r.request_id == new.request_id)
    problems = []
    if _safe(new) + TOLERANCE < min(_safe(base), request.requested_amount):
        problems.append(f"safe {new.amount_safe_to_pay} below min(base safe {base.amount_safe_to_pay}, new amount {request.requested_amount})")
    if _earliest(new) > _earliest(base):
        problems.append(f"earliest moved later {base.earliest_date_for_full_payment or '-'} -> {new.earliest_date_for_full_payment or '-'}")
    return "; ".join(problems) or None


def _unchanged(base: OutputRow, new: OutputRow, dataset: Dataset) -> str | None:
    changed = [column for column, value in base.as_csv_dict().items() if new.as_csv_dict()[column] != value]
    return f"changed columns {changed}" if changed else None


RELATIONS: list[tuple[str, str, Callable[[Dataset], Dataset], Check, int]] = [
    ("balance_up_10pct", "Raising the balance never lowers the safe amount or delays the earliest date.",
     lambda d: _scaled_profiles(d, "current_available_balance", Decimal("1.10")), _monotone(+1, -1), -1),
    ("minimum_up_10pct", "Raising the minimum balance never raises the safe amount or brings the earliest date forward.",
     lambda d: _scaled_profiles(d, "minimum_balance_to_keep", Decimal("1.10")), _monotone(-1, +1), +1),
    ("amount_down_10pct", "Lowering the requested amount keeps safe >= min(old safe, new amount) and never delays the earliest date.",
     lambda d: _scaled_requests(d, Decimal("0.90")), _amount_lowered, -1),
    ("pending_debit_added", "Adding a pending debit never raises the safe amount or brings the earliest date forward.",
     lambda d: _added_events(d, "pending_debit"), _monotone(-1, +1), +1),
    ("pending_credit_added", "Adding a pending credit changes nothing (pending credits are not counted).",
     lambda d: _added_events(d, "pending_credit"), _unchanged, 0),
    ("cancelled_debit_added", "Adding a cancelled debit changes nothing.",
     lambda d: _added_events(d, "cancelled_debit"), _unchanged, 0),
]


def run_metamorphic(dataset: Dataset) -> MetamorphicReport:
    base_rows = _run_rows(dataset)
    results = []
    for name, description, transform, check, status_direction in RELATIONS:
        transformed = transform(dataset)
        new_rows = _run_rows(transformed)
        result = RelationResult(relation=name, description=description, checked=len(new_rows))
        for request_id, new in new_rows.items():
            base = base_rows[request_id]
            detail = check(base, new, transformed)
            if detail:
                result.violations.append(RelationViolation(request_id=request_id, detail=detail))
            base_rank, new_rank = STATUS_RANK[base.affordability_status], STATUS_RANK[new.affordability_status]
            if (status_direction < 0 and new_rank > base_rank) or (status_direction > 0 and new_rank < base_rank):
                result.soft_status_regressions.append(
                    RelationViolation(request_id=request_id, detail=f"{base.affordability_status.value} -> {new.affordability_status.value}")
                )
        results.append(result)
    return MetamorphicReport(requests=len(base_rows), relations=results)


def render_metamorphic_report(report: MetamorphicReport) -> str:
    lines = [
        "# S3b metamorphic relations",
        "",
        f"- Requests per relation: {report.requests}",
        "",
        "| Relation | Violations | Soft status regressions |",
        "|---|---|---|",
        *[f"| {r.relation} | {len(r.violations)} | {len(r.soft_status_regressions)} |" for r in report.relations],
        "",
    ]
    for relation in report.relations:
        if relation.violations or relation.soft_status_regressions:
            lines += [f"## {relation.relation}", "", relation.description, ""]
            lines += [f"- {v.request_id}: {v.detail}" for v in relation.violations[:25]]
            lines += [f"- (soft) {v.request_id}: {v.detail}" for v in relation.soft_status_regressions[:10]]
            lines.append("")
    return "\n".join(lines)
