from datetime import date
from decimal import Decimal

from buyorwait.guardrails.input_checks import check_dataset
from buyorwait.schemas.enums import AffordabilityStatus, PaymentMethod, Severity


def test_row_counts(dataset):
    assert len(dataset.profiles) == 275
    assert len(dataset.events) == 25342
    assert len(dataset.requests) == 250
    assert len(dataset.sample_requests) == 25
    assert len(dataset.sample_expected) == 25
    assert len(dataset.payment_options) == 790
    assert len(dataset.messages) == 215
    assert len(dataset.images) == 16
    assert len(dataset.rates) == 134
    assert dataset.load_violations == []


def test_profile_pipe_lists_and_blank_installment_limit(dataset):
    user_02 = dataset.profiles["user_02"]
    assert user_02.payment_methods_user_will_consider == [PaymentMethod.PARTIAL_PAYMENT, PaymentMethod.INSTALLMENTS]
    assert user_02.expense_categories_to_protect == ["housing", "utilities", "education"]
    assert user_02.max_installment_months == 7
    assert dataset.profiles["user_01"].max_installment_months is None


def test_blank_amounts_stay_none(dataset):
    blank = [event for event in dataset.events if event.amount is None]
    assert len(blank) == 16
    assert all(dataset.images_by_event.get(event.event_id) for event in blank)


def test_payment_option_schedule_matches_sample_plan(dataset):
    option = next(o for o in dataset.payment_options if o.payment_option_id == "payment_option_05")
    assert [day for day, _ in option.schedule()] == [date(2025, 8, 8), date(2025, 9, 7), date(2025, 10, 7)]
    assert all(amount == Decimal("15952906.67") for _, amount in option.schedule())


def test_sample_labels_parsed(dataset):
    expected = dataset.sample_expected["request_02"]
    assert expected.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN
    assert expected.recommended_payment_method is PaymentMethod.INSTALLMENTS
    assert expected.earliest_date_for_full_payment == date(2025, 9, 15)
    assert dataset.sample_expected["request_05"].earliest_date_for_full_payment is None


def test_real_dataset_has_no_input_errors(dataset):
    errors = [violation for violation in check_dataset(dataset) if violation.severity is Severity.ERROR]
    assert errors == []
