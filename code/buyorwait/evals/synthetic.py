"""S2 synthetic scenarios: small cases with outputs computed by hand from the problem-statement rules.

Every case shares one user shape unless noted: request date 2025-03-05, a EUR balance, salary of 1000 settled on the
15th (Dec-Feb) and rent of 400 settled on the 1st (Dec-Mar). With no other events the 90-day path from the balance B is
B, +1000 (03-15), -400 (04-01), +1000 (04-15), -400 (05-01), +1000 (05-15), -400 (06-01): its lowest point is B itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from ..config import REPO_ROOT
from ..evidence.extract import EvidenceStore
from ..ingest.loaders import Dataset
from ..pipeline import EnginePipeline
from ..schemas.domain import ExpectedDecision, FinancialEvent, PaymentOption, Profile, PurchaseRequest
from ..schemas.evidence import AcceptedFact, EvidenceKind, EvidenceReview, IncomeSource
from .scoring import RequestEval, score_row

REQUEST_DATE = date(2025, 3, 5)


class SyntheticExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_safe_to_pay: str
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str


class SyntheticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    balance: Decimal
    minimum: Decimal
    amount: Decimal
    deadline: date
    methods: str
    max_installment_months: str = ""
    allows_partial: bool = False
    protect: str = "rent"
    reduce: str = ""
    stop: str = ""
    salary_history: bool = True
    rent_history: bool = True
    extra_events: list[dict[str, str]] = Field(default_factory=list)
    options: list[dict[str, str]] = Field(default_factory=list)
    image_amounts: dict[str, Decimal] = Field(default_factory=dict)
    expected: SyntheticExpectation


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    passed: bool
    evaluation: RequestEval


class SyntheticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: int
    passed: int
    results: list[CaseResult]


def event(event_id: str, day: str, amount: str, **overrides: str) -> dict[str, str]:
    row = {
        "event_id": event_id,
        "event_type": "expense",
        "description": "Monthly rent",
        "category": "rent",
        "direction": "debit",
        "amount": amount,
        "currency": "EUR",
        "event_date": day,
        "settlement_date": day,
        "status": "settled",
        "linked_event_id": "",
        "flexibility": "fixed",
        "minimum_allowed_amount": "",
    }
    row.update(overrides)
    return row


def option(option_id: str, amount: str, payments: int, first: str, frequency: str = "30", method: str = "installments") -> dict[str, str]:
    return {
        "payment_option_id": option_id,
        "payment_method": method,
        "payment_amount": amount,
        "number_of_payments": str(payments),
        "first_payment_date": first,
        "payment_frequency_days": frequency if payments > 1 else "",
        "financing_fee": "0",
        "total_payable_amount": str(Decimal(amount) * payments),
    }


def expect(safe: str, status: str, method: str, plan: str, earliest: str, changes: str = "none") -> SyntheticExpectation:
    return SyntheticExpectation(
        amount_safe_to_pay=safe,
        affordability_status=status,
        recommended_payment_method=method,
        payment_plan=plan,
        earliest_date_for_full_payment=earliest,
        spending_changes_needed=changes,
    )


def _subscriptions() -> list[dict[str, str]]:
    rows = []
    for name, category, flexibility, amount, floor in (
        ("streaming", "streaming", "stoppable", "30", ""),
        ("cloud", "cloud_storage", "stoppable", "15", ""),
        ("gym", "gym", "reducible", "40", "10"),
    ):
        for index, day in enumerate(("2024-12-08", "2025-01-08", "2025-02-08"), start=1):
            rows.append(
                event(
                    f"c5_{name}_{index}", day, amount, event_type="subscription", description=f"{name.title()} plan",
                    category=category, flexibility=flexibility, minimum_allowed_amount=floor,
                )
            )
    return rows


CASES: list[SyntheticCase] = [
    SyntheticCase(
        case_id="c01_affordable_now",
        description="Lowest headroom 2000-500=1500 covers 1000 today.",
        balance=Decimal("2000"), minimum=Decimal("500"), amount=Decimal("1000"), deadline=date(2025, 3, 31), methods="full_payment",
        expected=expect("1000", "affordable_now", "full_payment", "2025-03-05:1000", "2025-03-05"),
    ),
    SyntheticCase(
        case_id="c02_wait_for_salary_day",
        description="Safe today 500. On 03-15 later lows are 1600 -> capacity 1100 < 1200; on 04-15 later lows 2200 -> 1700. Wait until 04-15.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("1200"), deadline=date(2025, 4, 30), methods="full_payment",
        expected=expect("500", "affordable_later", "wait", "2025-04-15:1200", "2025-04-15"),
    ),
    SyntheticCase(
        case_id="c03_partial_beats_wait",
        description="As c02 with partial allowed: partial and wait both pay 1200 by the deadline; partial starts earlier.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("1200"), deadline=date(2025, 4, 30),
        methods="full_payment|partial_payment", allows_partial=True,
        expected=expect("500", "affordable_with_plan", "partial_payment", "2025-03-05:500|2025-04-15:700", "2025-04-15"),
    ),
    SyntheticCase(
        case_id="c04_installments_filtered",
        description="max 3 months drops the 4-payment option; the 2x450 option ends after the deadline; 2x470 (940) beats 3x320 (960).",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("900"), deadline=date(2025, 6, 30),
        methods="installments", max_installment_months="3",
        options=[
            option("payment_option_941", "250", 4, "2025-03-10"),
            option("payment_option_942", "320", 3, "2025-03-10"),
            option("payment_option_943", "470", 2, "2025-03-10"),
            option("payment_option_944", "450", 2, "2025-06-20"),
            option("payment_option_945", "900", 1, "2025-03-05", method="full_payment"),
        ],
        expected=expect("500", "affordable_with_plan", "installments", "2025-03-10:470|2025-04-09:470", "2025-03-15"),
    ),
    SyntheticCase(
        case_id="c05_smallest_saving_changes",
        description="Subscriptions on the 8th cut the low to 915 (safe 415); 470 is due before salary. Only gym->10 plus stop streaming "
        "(saves 60 before the low) is enough, and it is the cheapest sufficient set by 90-day saving.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("470"), deadline=date(2025, 3, 10), methods="full_payment",
        stop="streaming|cloud_storage", reduce="gym", extra_events=_subscriptions(),
        expected=expect("415", "affordable_with_plan", "full_payment", "2025-03-05:470", "2025-03-15", "stop:c5_streaming_3|reduce_to:c5_gym_3:10"),
    ),
    SyntheticCase(
        case_id="c06_not_affordable",
        description="Highest later capacity is 2300 < 5000, so no full payment date exists and partial/wait are impossible.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("5000"), deadline=date(2025, 4, 30),
        methods="full_payment|partial_payment", allows_partial=True,
        expected=expect("500", "not_affordable", "not_recommended", "none", ""),
    ),
    SyntheticCase(
        case_id="c07_pending_debit_reserved",
        description="Pending debit 300 settling 03-10 lowers the low to 700 (safe 200); on 03-15 capacity is 800.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("400"), deadline=date(2025, 3, 31), methods="full_payment",
        extra_events=[event("c07_pending", "2025-03-04", "300", description="Pending card charge", category="shopping", status="pending", settlement_date="2025-03-10")],
        expected=expect("200", "affordable_later", "wait", "2025-03-15:400", "2025-03-15"),
    ),
    SyntheticCase(
        case_id="c08_failed_and_cancelled_ignored",
        description="Failed and cancelled future debits never move cash; 450 is safe today.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("450"), deadline=date(2025, 3, 31), methods="full_payment",
        extra_events=[
            event("c08_failed", "2025-03-06", "2000", description="Failed bill payment", category="utilities", status="failed", settlement_date="2025-03-07"),
            event("c08_cancelled", "2025-03-08", "1500", description="Cancelled authorization", category="shopping", status="cancelled"),
        ],
        expected=expect("450", "affordable_now", "full_payment", "2025-03-05:450", "2025-03-05"),
    ),
    SyntheticCase(
        case_id="c09_pending_credit_ignored",
        description="A pending refund of 600 on 03-08 is not counted, so full payment of 800 waits for 03-15.",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("800"), deadline=date(2025, 3, 31), methods="full_payment",
        extra_events=[event("c09_refund", "2025-03-04", "600", event_type="refund", description="Pending merchant refund", category="shopping", direction="credit", status="pending", settlement_date="2025-03-08")],
        expected=expect("500", "affordable_later", "wait", "2025-03-15:800", "2025-03-15"),
    ),
    SyntheticCase(
        case_id="c10_blank_amount_from_image",
        description="Scheduled bill with blank amount; the validated image amount 350 is reserved on 03-12 (safe 150).",
        balance=Decimal("1000"), minimum=Decimal("500"), amount=Decimal("300"), deadline=date(2025, 3, 31), methods="full_payment",
        extra_events=[event("c10_bill", "2025-03-05", "", description="Water bill due", category="utilities", status="scheduled", settlement_date="2025-03-12")],
        image_amounts={"c10_bill": Decimal("350")},
        expected=expect("150", "affordable_later", "wait", "2025-03-15:300", "2025-03-15"),
    ),
    SyntheticCase(
        case_id="c11_full_safe_but_not_accepted",
        description="Full payment is safe today, but the user only considers installments (3x210 within 6 months).",
        balance=Decimal("2000"), minimum=Decimal("500"), amount=Decimal("600"), deadline=date(2025, 6, 30),
        methods="installments", max_installment_months="6",
        options=[option("payment_option_1101", "210", 3, "2025-03-10"), option("payment_option_1102", "600", 1, "2025-03-05", method="full_payment")],
        expected=expect("600", "affordable_with_plan", "installments", "2025-03-10:210|2025-04-09:210|2025-05-09:210", "2025-03-05"),
    ),
    SyntheticCase(
        case_id="c12_confirmed_salary_counts",
        description="First job: only a prorated salary settled, plus a scheduled confirmed salary of 1000 on 03-15. Safe 100; full 800 on 03-15.",
        balance=Decimal("600"), minimum=Decimal("500"), amount=Decimal("800"), deadline=date(2025, 4, 30), methods="full_payment",
        salary_history=False, rent_history=False,
        extra_events=[
            event("c12_prorated", "2025-02-15", "500", event_type="income", description="Prorated first salary", category="salary", direction="credit"),
            event("c12_next_salary", "2025-03-15", "1000", event_type="income", description="Next confirmed salary", category="salary", direction="credit", status="scheduled"),
        ],
        expected=expect("100", "affordable_later", "wait", "2025-03-15:800", "2025-03-15"),
    ),
]


def build_case_dataset(case: SyntheticCase) -> tuple[Dataset, PurchaseRequest, EvidenceStore | None]:
    user_id = f"syn_{case.case_id}"
    request_id = f"request_{case.case_id}"
    rows: list[dict[str, str]] = []
    if case.salary_history:
        rows += [
            event(f"{case.case_id}_salary_{i}", day, "1000", event_type="income", description="Payroll credit", category="salary", direction="credit")
            for i, day in enumerate(("2024-12-15", "2025-01-15", "2025-02-15"), start=1)
        ]
    if case.rent_history:
        rows += [event(f"{case.case_id}_rent_{i}", day, "400") for i, day in enumerate(("2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01"), start=1)]
    rows += case.extra_events
    events = [FinancialEvent.model_validate({**row, "user_id": user_id}) for row in rows]
    profile = Profile.model_validate(
        {
            "user_id": user_id,
            "home_currency": "EUR",
            "current_available_balance": str(case.balance),
            "minimum_balance_to_keep": str(case.minimum),
            "financial_priorities": "",
            "expense_categories_to_protect": case.protect,
            "expense_categories_user_is_willing_to_reduce": case.reduce,
            "expense_categories_user_is_willing_to_stop": case.stop,
            "payment_methods_user_will_consider": case.methods,
            "max_installment_months": case.max_installment_months,
        }
    )
    request = PurchaseRequest.model_validate(
        {
            "request_id": request_id,
            "user_id": user_id,
            "request_date": REQUEST_DATE.isoformat(),
            "request_type": "purchase",
            "requested_amount": str(case.amount),
            "desired_completion_date": case.deadline.isoformat(),
            "allows_partial_payment": "true" if case.allows_partial else "false",
            "request_text": "synthetic",
        }
    )
    options = [PaymentOption.model_validate({**row, "request_id": request_id}) for row in case.options]
    dataset = Dataset(
        root=REPO_ROOT / "dataset",
        profiles={user_id: profile},
        events=events,
        rates=[],
        requests=[request],
        sample_requests=[],
        sample_expected={},
        payment_options=options,
        messages=[],
        images=[],
        template_request_ids=[request_id],
        load_violations=[],
    )
    evidence = None
    if case.image_amounts:
        facts = [
            AcceptedFact(
                fact_id=f"syn_image_{event_id}#amount", source_id=f"syn_image_{event_id}", user_id=user_id, request_id=request_id,
                related_event_id=event_id, sent_on=None, source_authority=None, kind=EvidenceKind.DOCUMENT_AMOUNT,
                income_source=IncomeSource.NOT_APPLICABLE, original_amount=amount, currency="EUR", amount_home=amount,
                percent=None, effective_date=None, category=None, confidence_score=0.95, quotes=("synthetic",),
            )
            for event_id, amount in case.image_amounts.items()
        ]
        evidence = EvidenceStore({"syn_image": EvidenceReview(source_id="syn_image", source_type="image", user_id=user_id, accepted=facts)})
    return dataset, request, evidence


def run_synthetic(cases: list[SyntheticCase] = CASES) -> SyntheticReport:
    results = []
    for case in cases:
        dataset, request, evidence = build_case_dataset(case)
        row = EnginePipeline(dataset, evidence=evidence).run_request(request).row
        expected = ExpectedDecision.model_validate(
            {"request_id": request.request_id, **case.expected.model_dump(), "decision_explanation": case.description}
        )
        evaluation = score_row(expected, row)
        results.append(CaseResult(case_id=case.case_id, description=case.description, passed=evaluation.all_match, evaluation=evaluation))
    return SyntheticReport(cases=len(results), passed=sum(result.passed for result in results), results=results)


def render_synthetic_report(report: SyntheticReport) -> str:
    lines = ["# S2 synthetic scenarios", "", f"- Passed {report.passed}/{report.cases}", "", "| Case | Result | Mismatched fields |", "|---|---|---|"]
    for result in report.results:
        misses = "; ".join(
            f"{field.field}: expected {field.expected or '(blank)'} got {field.actual or '(blank)'}"
            for field in result.evaluation.fields
            if not field.match
        )
        lines.append(f"| {result.case_id} | {'pass' if result.passed else 'FAIL'} | {misses or '-'} |")
    return "\n".join(lines) + "\n"
