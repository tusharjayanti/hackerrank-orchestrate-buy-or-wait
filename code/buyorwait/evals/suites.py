"""Eval suites: S1 (sample labels) and S3 (label-free invariants over a written output.csv)."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from ..guardrails.contract import check_output_row
from ..ingest.loaders import Dataset
from ..output.writer import OUTPUT_COLUMNS, OutputRow
from ..pipeline import EnginePipeline, RequestResult
from ..schemas.enums import Severity
from ..schemas.obs import GuardrailViolation
from .scoring import EvalReport, build_report, score_row


def run_samples(pipeline: EnginePipeline, run_id: str) -> tuple[EvalReport, list[RequestResult]]:
    results = [pipeline.run_request(request) for request in pipeline.dataset.sample_requests]
    evaluations = [
        score_row(pipeline.dataset.sample_expected[result.request.request_id], result.row, result.fell_back)
        for result in results
    ]
    return build_report(run_id, "samples", pipeline.knobs.model_dump(mode="json"), evaluations), results


def run_invariants(dataset: Dataset, output_path: Path) -> list[GuardrailViolation]:
    """G9 completeness plus G4 contract checks on every row of a written output file."""
    violations: list[GuardrailViolation] = []

    def fail(code: str, message: str, request_id: str | None = None) -> None:
        violations.append(
            GuardrailViolation(layer="G9", code=code, severity=Severity.ERROR, message=message, request_id=request_id)
        )

    if not output_path.exists():
        fail("output_missing", f"{output_path} does not exist")
        return violations

    with output_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    if header != OUTPUT_COLUMNS:
        fail("bad_header", f"columns {header} differ from {OUTPUT_COLUMNS}")

    ids = [row.get("request_id", "") for row in rows]
    expected_ids = [request.request_id for request in dataset.requests]
    duplicates = [request_id for request_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        fail("duplicate_rows", f"duplicate request_ids: {duplicates[:5]}")
    if sorted(ids) != sorted(expected_ids):
        fail("request_set_mismatch", f"{len(set(expected_ids) - set(ids))} missing, {len(set(ids) - set(expected_ids))} unexpected")

    requests = {request.request_id: request for request in dataset.requests}
    for raw in rows:
        request = requests.get(raw.get("request_id", ""))
        if request is None:
            continue
        try:
            row = OutputRow.model_validate(raw)
        except ValidationError as exc:
            fail("invalid_row", exc.errors(include_url=False)[0]["msg"], request.request_id)
            continue
        violations.extend(
            check_output_row(
                row,
                request,
                dataset.profiles[request.user_id],
                dataset.options_by_request.get(request.request_id, []),
                dataset.events_by_id,
            )
        )
    return violations
