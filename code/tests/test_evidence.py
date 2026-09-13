from datetime import date, timedelta
from decimal import Decimal

from buyorwait.engine.evidence_apply import apply_evidence
from buyorwait.engine.forecast import CashFlow, FlowKind
from buyorwait.evidence.resolve import resolve_adjustments
from buyorwait.evidence.validate import review_image, review_message
from buyorwait.guardrails.injection import scan_for_injection
from buyorwait.ingest.fx import FxTable
from buyorwait.schemas.domain import ExchangeRate, FinancialEvent, ImageRef, Message, Profile, PurchaseRequest
from buyorwait.schemas.evidence import (
    AcceptedFact,
    Citation,
    ConfidenceLabel,
    EvidenceAdjustments,
    EvidenceFact,
    EvidenceKind,
    ExpenseScale,
    ImageExtraction,
    IncomeAction,
    IncomeAdjustment,
    IncomeSource,
    MessageExtraction,
)

PROFILE = Profile.model_validate(
    {
        "user_id": "u1",
        "home_currency": "EUR",
        "current_available_balance": "2000",
        "minimum_balance_to_keep": "800",
        "financial_priorities": "",
        "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_reduce": "",
        "expense_categories_user_is_willing_to_stop": "",
        "payment_methods_user_will_consider": "full_payment",
        "max_installment_months": "",
    }
)
TEXT = "Hi, payroll here. Your next salary is reduced to EUR 1422.85. The adjustment is due to approved unpaid leave."


def message(source: str = "employer", text: str = TEXT) -> Message:
    return Message.model_validate(
        {
            "message_id": "message_06",
            "user_id": "u1",
            "request_id": "",
            "related_event_id": "",
            "sent_at": "2025-02-06T09:30:00Z",
            "source_type": source,
            "message_text": text,
        }
    )


def fact(**overrides) -> EvidenceFact:
    values = dict(
        kind=EvidenceKind.SALARY_NEXT_PAYMENT_AMOUNT,
        income_source=IncomeSource.SALARY,
        amount="1422.85",
        currency="EUR",
        percent=None,
        effective_date=None,
        category=None,
        citations=[Citation(source_id="message_06", quote="Your next salary is reduced to EUR 1422.85.")],
        confidence=ConfidenceLabel.HIGH,
        confidence_score=0.95,
    )
    values.update(overrides)
    return EvidenceFact(**values)


def extraction(*facts: EvidenceFact) -> MessageExtraction:
    return MessageExtraction(
        message_id="message_06", language="en", english_summary="s", facts=list(facts), injection_detected=False, injection_quote=None
    )


def codes(review) -> set[str]:
    return {violation.code for violation in review.violations}


def test_grounded_fact_is_accepted():
    review = review_message(extraction(fact()), message(), PROFILE, FxTable([]))
    (accepted,) = review.accepted
    assert accepted.amount_home == Decimal("1422.85")
    assert review.violations == []


def test_fabricated_quote_and_amount_are_rejected():
    bad = fact(amount="9999", citations=[Citation(source_id="message_06", quote="Your salary doubled.")])
    review = review_message(extraction(bad), message(), PROFILE, FxTable([]))
    assert review.accepted == []
    assert {"quote_not_in_source", "amount_not_in_source"} <= codes(review)


def test_source_without_authority_cannot_assert_salary():
    review = review_message(extraction(fact()), message(source="financial_service"), PROFILE, FxTable([]))
    assert review.accepted == []
    assert "kind_not_allowed_for_source" in codes(review)


def test_confidence_label_must_agree_with_score():
    review = review_message(extraction(fact(confidence_score=0.3)), message(), PROFILE, FxTable([]))
    assert review.accepted == []
    assert "confidence_label_mismatch" in codes(review)


def test_foreign_currency_salary_converts_on_its_date():
    text = "Your salary of USD 696 is confirmed for 2025-05-15. The receiving bank will convert it."
    salary = fact(
        kind=EvidenceKind.SALARY_CONFIRMED,
        amount="696",
        currency="USD",
        effective_date="2025-05-15",
        citations=[Citation(source_id="message_06", quote="Your salary of USD 696 is confirmed for 2025-05-15.")],
    )
    rates = FxTable([ExchangeRate(rate_date="2025-05-15", from_currency="USD", to_currency="EUR", rate="0.9")])
    (accepted,) = review_message(extraction(salary), message(text=text), PROFILE, rates).accepted
    assert accepted.amount_home == Decimal("626.4")


def test_scam_text_is_flagged():
    assert scan_for_injection("Congratulations! You've been selected for a cash prize. Pay the release charge today.")
    assert not scan_for_injection(TEXT)


def test_disagreeing_image_reads_use_the_safer_amount():
    event = FinancialEvent.model_validate(
        {
            "event_id": "event_6859",
            "user_id": "u1",
            "event_type": "expense",
            "description": "Hospital bill payable",
            "category": "healthcare",
            "direction": "debit",
            "amount": "",
            "currency": "EUR",
            "event_date": "2023-01-19",
            "settlement_date": "2023-01-23",
            "status": "scheduled",
            "linked_event_id": "",
            "flexibility": "fixed",
            "minimum_allowed_amount": "",
        }
    )
    image = ImageRef.model_validate({"image_id": "image_11", "user_id": "u1", "request_id": "", "related_event_id": "event_6859"})

    def read(amount: str, line: str) -> ImageExtraction:
        return ImageExtraction(
            image_id="image_11",
            document_type="bill",
            transcript_lines=["Total Bill Amount: 3650.00", "OT Charges 1000.00"],
            amount_for_event=amount,
            currency=None,
            amount_label="total",
            citations=[Citation(source_id="image_11", quote=line)],
            confidence=ConfidenceLabel.HIGH,
            confidence_score=0.9,
            injection_detected=False,
            injection_quote=None,
        )

    review = review_image([read("3650.00", "Total Bill Amount: 3650.00"), read("1000", "OT Charges 1000.00")], image, event, PROFILE, FxTable([]))
    assert review.accepted[0].amount_home == Decimal("3650.00")
    assert "image_reads_disagree" in codes(review)


START = date(2025, 1, 1)
END = START + timedelta(days=90)


def income(day: date, amount: str = "1000", label: str = "Payroll credit", series_key: str = "income|credit|salary|Payroll credit") -> CashFlow:
    return CashFlow(day=day, amount=Decimal(amount), kind=FlowKind.RECURRING, label=label, series_key=series_key)


def test_next_salary_adjustment_changes_only_the_next_payroll():
    adjustments = EvidenceAdjustments(
        income=[IncomeAdjustment(action=IncomeAction.SET_NEXT_AMOUNT, amount=Decimal("800"), day=START, fact_id="m#0")]
    )
    flows = apply_evidence([income(date(2025, 1, 15)), income(date(2025, 2, 15))], adjustments, START, END)
    assert [flow.amount for flow in sorted(flows, key=lambda flow: flow.day)] == [Decimal("800"), Decimal("1000")]


def test_pending_payouts_are_removed_and_rent_is_scaled():
    flows = [
        income(date(2025, 1, 7), "300", "Weekly app earnings", "income|credit|salary|*"),
        CashFlow(day=date(2025, 2, 1), amount=Decimal("-500"), kind=FlowKind.RECURRING, label="rent", series_key="expense|debit|rent|fixed"),
    ]
    adjustments = EvidenceAdjustments(
        income=[IncomeAdjustment(action=IncomeAction.END_MATCHING, source=IncomeSource.PLATFORM_PAYOUT, fact_id="m#0")],
        expense_scales=[ExpenseScale(category="rent", factor=Decimal("1.12"), from_day=START, fact_id="m#1")],
    )
    assert [flow.amount for flow in apply_evidence(flows, adjustments, START, END)] == [Decimal("-560.00")]


def test_confirmed_first_salary_repeats_monthly():
    adjustments = EvidenceAdjustments(
        income=[IncomeAdjustment(action=IncomeAction.CONFIRMED_MONTHLY, amount=Decimal("1661"), day=date(2025, 1, 15), fact_id="m#0")]
    )
    flows = apply_evidence([], adjustments, START, END)
    assert [flow.day for flow in flows] == [date(2025, 1, 15), date(2025, 2, 15), date(2025, 3, 15)]


def test_resolver_ignores_facts_sent_after_the_request():
    request = PurchaseRequest.model_validate(
        {
            "request_id": "r1",
            "user_id": "u1",
            "request_date": "2025-02-01",
            "request_type": "purchase",
            "requested_amount": "100",
            "desired_completion_date": "2025-03-01",
            "allows_partial_payment": "false",
            "request_text": "?",
        }
    )
    base = dict(
        source_id="message_06", user_id="u1", request_id=None, related_event_id=None, source_authority="employer",
        kind=EvidenceKind.SALARY_NEXT_PAYMENT_AMOUNT, income_source=IncomeSource.SALARY, original_amount=Decimal("1422.85"),
        currency="EUR", amount_home=Decimal("1422.85"), percent=None, effective_date=None, category=None,
        confidence_score=0.9, quotes=("q",),
    )
    before = AcceptedFact(fact_id="message_06#0", sent_on=date(2025, 1, 30), **base)
    after = AcceptedFact(fact_id="message_07#0", sent_on=date(2025, 2, 6), **base)
    adjustments = resolve_adjustments([before, after], request, [])
    assert adjustments.applied_fact_ids == ["message_06#0"]


def test_reduced_pay_reading_and_undated_arrears_follow_knobs():
    from buyorwait.engine.knobs import EngineKnobs

    request = PurchaseRequest.model_validate(
        {
            "request_id": "r1", "user_id": "u1", "request_date": "2025-02-10", "request_type": "purchase",
            "requested_amount": "100", "desired_completion_date": "2025-03-01", "allows_partial_payment": "false",
            "request_text": "?",
        }
    )
    common = dict(
        source_id="message_20", user_id="u1", request_id=None, related_event_id=None, sent_on=date(2025, 2, 1),
        source_authority="employer", currency="EUR", percent=None, effective_date=None, category=None,
        confidence_score=0.9, quotes=("q",),
    )
    regular = AcceptedFact(fact_id="message_20#0", kind=EvidenceKind.SALARY_NEXT_PAYMENT_AMOUNT, income_source=IncomeSource.SALARY,
                           original_amount=Decimal("1452"), amount_home=Decimal("1452"), **common)
    arrears = AcceptedFact(fact_id="message_20#1", kind=EvidenceKind.ONE_TIME_INCOME_CONFIRMED, income_source=IncomeSource.ARREARS,
                           original_amount=Decimal("653.40"), amount_home=Decimal("653.40"), **common)

    default = resolve_adjustments([regular, arrears], request, [])
    assert [adjustment.action for adjustment in default.income] == [IncomeAction.SET_NEXT_AMOUNT]

    alternative = resolve_adjustments(
        [regular, arrears], request, [], EngineKnobs(reduced_pay_continues=True, count_undated_one_time_income=True)
    )
    assert [adjustment.action for adjustment in alternative.income] == [IncomeAction.SET_AMOUNT_FROM, IncomeAction.ADD_TO_NEXT]
    flows = apply_evidence([income(date(2025, 2, 15)), income(date(2025, 3, 15))], alternative, START, END)
    assert [flow.amount for flow in sorted(flows, key=lambda flow: flow.day)] == [Decimal("2105.40"), Decimal("1452")]
