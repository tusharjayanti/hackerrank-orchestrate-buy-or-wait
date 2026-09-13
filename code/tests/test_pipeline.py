from decimal import Decimal

import pytest

from buyorwait.evals.scoring import score_row
from buyorwait.guardrails.contract import check_output_row
from buyorwait.output.writer import OutputRow
from buyorwait.pipeline import EnginePipeline


@pytest.fixture(scope="session")
def pipeline(dataset, ledger) -> EnginePipeline:
    return EnginePipeline(dataset, ledger=ledger)


def test_every_request_produces_a_contract_valid_row(pipeline, dataset):
    for request in dataset.all_requests:
        result = pipeline.run_request(request)
        assert result.violations == [], (request.request_id, [violation.code for violation in result.violations])


def test_contract_rejects_broken_rows(pipeline, dataset):
    request = dataset.sample_requests[0]
    result = pipeline.run_request(request)
    broken = result.row.model_copy(
        update={
            "amount_safe_to_pay": str(request.requested_amount + Decimal(1)),
            "payment_plan": "2030-01-01:5|2020-01-01:5",
        }
    )
    codes = {
        violation.code
        for violation in check_output_row(
            broken,
            request,
            dataset.profiles[request.user_id],
            dataset.options_by_request[request.request_id],
            dataset.events_by_id,
        )
    }
    assert {"safe_amount_out_of_bounds", "plan_not_chronological"} <= codes


def test_scoring_normalises_equivalent_formats(dataset):
    expected = dataset.sample_expected["request_21"]
    row = OutputRow(
        request_id="request_21",
        amount_safe_to_pay="1543.35",
        affordability_status=expected.affordability_status,
        recommended_payment_method=expected.recommended_payment_method,
        payment_plan="2026-04-03:1574.4",
        earliest_date_for_full_payment="2026-04-15",
        spending_changes_needed="reduce_to:event_1816:23.5|stop:event_1815",
        decision_explanation="x",
    )
    assert score_row(expected, row).all_match
