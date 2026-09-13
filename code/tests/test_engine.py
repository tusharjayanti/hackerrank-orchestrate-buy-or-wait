from datetime import date, timedelta
from decimal import Decimal

from buyorwait.engine.forecast import CashFlow, FlowKind, Timeline
from buyorwait.engine.plans import PlanKind, build_candidates
from buyorwait.engine.recurrence import add_months
from buyorwait.engine.spending import SpendingAction, SpendingChange, change_combinations
from buyorwait.formatting import format_money, format_plan_amount, format_safe_amount
from buyorwait.schemas.domain import PaymentOption, Profile, PurchaseRequest

START = date(2025, 1, 1)


def flow(offset: int, amount: str, series_key: str | None = None) -> CashFlow:
    return CashFlow(
        day=START + timedelta(days=offset), amount=Decimal(amount), kind=FlowKind.RECURRING, label="x", series_key=series_key
    )


def timeline(flows, opening="1000", minimum="500") -> Timeline:
    return Timeline(Decimal(opening), Decimal(minimum), flows, START, START + timedelta(days=90), ("credit", "payment", "debit"))


def test_capacity_is_lowest_later_headroom():
    tl = timeline([flow(5, "-300"), flow(14, "800"), flow(20, "-100")])
    assert tl.capacity_on(START) == Decimal("200")
    assert tl.capacity_on(START + timedelta(days=14)) == Decimal("900")
    assert tl.earliest_day_for(Decimal("500")) == START + timedelta(days=14)


def test_capacity_is_none_after_an_earlier_breach():
    tl = timeline([flow(3, "-600"), flow(10, "1000")])
    assert tl.capacity_on(START) == Decimal("-100")
    assert tl.capacity_on(START + timedelta(days=12)) is None


def test_same_day_credit_lands_before_payment_and_debits():
    tl = timeline([flow(15, "1000"), flow(15, "-200")], opening="600")
    assert tl.capacity_on(START + timedelta(days=15)) == Decimal("900")


def test_simulate_reports_first_breach():
    tl = timeline([flow(5, "-300")])
    result = tl.simulate([CashFlow(day=START, amount=Decimal("-250"), kind=FlowKind.PAYMENT, label="p")])
    assert not result.safe
    assert result.first_breach_day == START + timedelta(days=5)


def test_add_months_keeps_anchor_day():
    assert add_months(date(2025, 1, 31), 1, 31) == date(2025, 2, 28)
    assert add_months(date(2025, 2, 28), 1, 31) == date(2025, 3, 31)


def make_profile(methods: str, max_months: str = "") -> Profile:
    return Profile.model_validate(
        {
            "user_id": "u1",
            "home_currency": "EUR",
            "current_available_balance": "1000",
            "minimum_balance_to_keep": "100",
            "financial_priorities": "",
            "expense_categories_to_protect": "",
            "expense_categories_user_is_willing_to_reduce": "",
            "expense_categories_user_is_willing_to_stop": "",
            "payment_methods_user_will_consider": methods,
            "max_installment_months": max_months,
        }
    )


def make_request(allows_partial: str = "true") -> PurchaseRequest:
    return PurchaseRequest.model_validate(
        {
            "request_id": "r1",
            "user_id": "u1",
            "request_date": "2025-01-01",
            "request_type": "purchase",
            "requested_amount": "1000",
            "desired_completion_date": "2025-02-01",
            "allows_partial_payment": allows_partial,
            "request_text": "Can I afford it?",
        }
    )


def make_option(option_id: str, payments: int, amount: str = "350") -> PaymentOption:
    return PaymentOption.model_validate(
        {
            "payment_option_id": option_id,
            "request_id": "r1",
            "payment_method": "installments",
            "payment_amount": amount,
            "number_of_payments": str(payments),
            "first_payment_date": "2025-01-05",
            "payment_frequency_days": "30",
            "financing_fee": "50",
            "total_payable_amount": str(Decimal(amount) * payments),
        }
    )


def test_partial_needs_permission_and_a_safe_date_before_deadline():
    profile = make_profile("partial_payment")
    (partial,), _ = build_candidates(make_request(), profile, [], Decimal("400"), date(2025, 1, 15))
    assert partial.kind is PlanKind.PARTIAL
    assert [(p.day, p.amount) for p in partial.payments] == [
        (date(2025, 1, 1), Decimal("400")),
        (date(2025, 1, 15), Decimal("600")),
    ]
    late, excluded = build_candidates(make_request(), profile, [], Decimal("400"), date(2025, 2, 2))
    assert late == [] and any("desired_completion_date" in item.reason for item in excluded)
    not_allowed, _ = build_candidates(make_request("false"), profile, [], Decimal("400"), date(2025, 1, 15))
    assert not_allowed == []


def test_installments_respect_methods_and_max_months():
    options = [make_option("payment_option_7", 3), make_option("payment_option_8", 6, amount="180")]
    candidates, _ = build_candidates(make_request(), make_profile("installments", "4"), options, Decimal("0"), None)
    assert [candidate.payment_option_id for candidate in candidates] == ["payment_option_7"]
    candidates, _ = build_candidates(make_request(), make_profile("full_payment"), options, Decimal("0"), None)
    assert [candidate.kind for candidate in candidates] == [PlanKind.FULL_NOW]


def change(series_key: str, action: SpendingAction, saving: str, event_id: str) -> SpendingChange:
    return SpendingChange(
        action=action,
        event_id=event_id,
        series_key=series_key,
        category="c",
        description="d",
        new_amount=Decimal("23.5") if action is SpendingAction.REDUCE_TO else None,
        saving_per_occurrence=Decimal(saving),
    )


def test_change_combinations_prefer_smallest_total_saving():
    backup = change("backup", SpendingAction.STOP, "11", "event_1815")
    reduce_streaming = change("streaming", SpendingAction.REDUCE_TO, "23.5", "event_1816")
    stop_streaming = change("streaming", SpendingAction.STOP, "47", "event_1816")
    flows = [flow(9, "-47", "streaming"), flow(12, "-11", "backup")]
    combos = change_combinations([backup, reduce_streaming, stop_streaming], 3, flows)
    tokens = [[item.token for item in combo] for combo in combos]
    assert tokens[:4] == [
        ["stop:event_1815"],
        ["reduce_to:event_1816:23.50"],
        ["stop:event_1815", "reduce_to:event_1816:23.50"],
        ["stop:event_1816"],
    ]
    assert all(len({item.series_key for item in combo}) == len(combo) for combo in combos)


def test_formatting_matches_sample_conventions():
    assert format_plan_amount(Decimal("25256")) == "25256"
    assert format_plan_amount(Decimal("620.4")) == "620.40"
    assert format_safe_amount(Decimal("17229139.20")) == "17229139.2"
    assert format_safe_amount(Decimal("0")) == "0"
    assert format_money(Decimal("15952906.67"), "IDR") == "IDR 15,952,906.67"
    assert format_money(Decimal("93000"), "INR") == "INR 93,000"
