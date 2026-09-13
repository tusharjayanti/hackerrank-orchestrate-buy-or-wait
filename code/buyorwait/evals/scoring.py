"""S1: compare output rows with sample labels field by field."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..guardrails.contract import ContractParseError, parse_changes, parse_plan
from ..output.writer import OutputRow
from ..schemas.domain import ExpectedDecision

TOLERANCE = Decimal("0.01")


class ScoredField(StrEnum):
    AMOUNT = "amount_safe_to_pay"
    STATUS = "affordability_status"
    METHOD = "recommended_payment_method"
    PLAN = "payment_plan"
    EARLIEST = "earliest_date_for_full_payment"
    CHANGES = "spending_changes_needed"


class FieldResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: ScoredField
    expected: str
    actual: str
    match: bool


class RequestEval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    fields: list[FieldResult]
    expected_explanation: str
    actual_explanation: str
    fell_back: bool = False

    @property
    def all_match(self) -> bool:
        return all(result.match for result in self.fields)


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite: str
    knobs: dict[str, Any]
    field_accuracy: dict[str, float]
    composite: float
    exact_rows: int
    requests: list[RequestEval]


def _plans_equal(expected: str, actual: str) -> bool:
    try:
        left, right = parse_plan(expected), parse_plan(actual)
    except ContractParseError:
        return False
    return len(left) == len(right) and all(
        a_day == b_day and abs(a_amount - b_amount) <= TOLERANCE for (a_day, a_amount), (b_day, b_amount) in zip(left, right)
    )


def _changes_equal(expected: str, actual: str) -> bool:
    try:
        return sorted(parse_changes(expected), key=str) == sorted(parse_changes(actual), key=str)
    except ContractParseError:
        return False


def _amounts_equal(expected: Decimal, actual: str) -> bool:
    try:
        return abs(Decimal(actual) - expected) <= TOLERANCE
    except InvalidOperation:
        return False


def score_row(expected: ExpectedDecision, row: OutputRow, fell_back: bool = False) -> RequestEval:
    expected_earliest = expected.earliest_date_for_full_payment.isoformat() if expected.earliest_date_for_full_payment else ""
    checks = [
        (ScoredField.AMOUNT, str(expected.amount_safe_to_pay), row.amount_safe_to_pay, _amounts_equal(expected.amount_safe_to_pay, row.amount_safe_to_pay)),
        (ScoredField.STATUS, expected.affordability_status.value, row.affordability_status.value, expected.affordability_status is row.affordability_status),
        (ScoredField.METHOD, expected.recommended_payment_method.value, row.recommended_payment_method.value, expected.recommended_payment_method is row.recommended_payment_method),
        (ScoredField.PLAN, expected.payment_plan, row.payment_plan, _plans_equal(expected.payment_plan, row.payment_plan)),
        (ScoredField.EARLIEST, expected_earliest, row.earliest_date_for_full_payment, expected_earliest == row.earliest_date_for_full_payment),
        (ScoredField.CHANGES, expected.spending_changes_needed, row.spending_changes_needed, _changes_equal(expected.spending_changes_needed, row.spending_changes_needed)),
    ]
    return RequestEval(
        request_id=expected.request_id,
        fields=[FieldResult(field=field, expected=want, actual=got, match=match) for field, want, got, match in checks],
        expected_explanation=expected.decision_explanation,
        actual_explanation=row.decision_explanation,
        fell_back=fell_back,
    )


def build_report(run_id: str, suite: str, knobs: dict[str, Any], evaluations: Sequence[RequestEval]) -> EvalReport:
    count = max(len(evaluations), 1)
    accuracy = {
        field.value: sum(next(r.match for r in evaluation.fields if r.field is field) for evaluation in evaluations) / count
        for field in ScoredField
    }
    return EvalReport(
        run_id=run_id,
        suite=suite,
        knobs=knobs,
        field_accuracy=accuracy,
        composite=sum(accuracy.values()) / len(accuracy),
        exact_rows=sum(evaluation.all_match for evaluation in evaluations),
        requests=list(evaluations),
    )


def render_markdown(report: EvalReport) -> str:
    lines = [
        f"# Eval report: {report.suite}",
        "",
        f"- Run: `{report.run_id}`",
        f"- Composite (mean field exact-match): **{report.composite:.3f}**",
        f"- Rows fully correct: {report.exact_rows}/{len(report.requests)}",
        "",
        "| Field | Accuracy |",
        "|---|---|",
        *[f"| {field} | {accuracy:.3f} |" for field, accuracy in report.field_accuracy.items()],
        "",
        "## Mismatches",
        "",
        "| Request | Field | Expected | Actual |",
        "|---|---|---|---|",
    ]
    for evaluation in report.requests:
        for result in evaluation.fields:
            if not result.match:
                lines.append(f"| {evaluation.request_id} | {result.field} | {result.expected or '(blank)'} | {result.actual or '(blank)'} |")
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (directory / "eval_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    with (directory / "per_request.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["request_id", "field", "expected", "actual", "match"])
        for evaluation in report.requests:
            for result in evaluation.fields:
                writer.writerow([evaluation.request_id, result.field, result.expected, result.actual, result.match])
