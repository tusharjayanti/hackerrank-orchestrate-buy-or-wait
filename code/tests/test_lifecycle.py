from decimal import Decimal

from buyorwait.ingest.fx import FxTable
from buyorwait.ingest.lifecycle import build_ledger
from buyorwait.schemas.domain import ExchangeRate, FinancialEvent, Profile
from buyorwait.schemas.enums import CashTreatment, LifecycleRole

PROFILE = Profile.model_validate(
    {
        "user_id": "u1",
        "home_currency": "INR",
        "current_available_balance": "1000",
        "minimum_balance_to_keep": "100",
        "financial_priorities": "",
        "expense_categories_to_protect": "rent",
        "expense_categories_user_is_willing_to_reduce": "",
        "expense_categories_user_is_willing_to_stop": "",
        "payment_methods_user_will_consider": "full_payment",
        "max_installment_months": "",
    }
)


def event(event_id: str, **overrides: str) -> FinancialEvent:
    row = {
        "event_id": event_id,
        "user_id": "u1",
        "event_type": "expense",
        "description": "Purchase",
        "category": "shopping",
        "direction": "debit",
        "amount": "100",
        "currency": "INR",
        "event_date": "2024-01-10",
        "settlement_date": "2024-01-10",
        "status": "settled",
        "linked_event_id": "",
        "flexibility": "fixed",
        "minimum_allowed_amount": "",
    }
    row.update(overrides)
    return FinancialEvent.model_validate(row)


def ledger_for(*events: FinancialEvent, rates: tuple[ExchangeRate, ...] = ()):
    entries = build_ledger(events, {"u1": PROFILE}, FxTable(rates))["u1"]
    return {entry.event_id: entry for entry in entries}


def test_cancelled_authorization_then_settled_purchase_counts_once():
    ledger = ledger_for(event("auth", status="cancelled"), event("buy", linked_event_id="auth"))
    assert ledger["auth"].treatment is CashTreatment.IGNORED_CANCELLED
    assert ledger["auth"].lifecycle_role is LifecycleRole.CHAIN_ORIGIN
    assert ledger["buy"].treatment is CashTreatment.SETTLED
    assert ledger["buy"].lifecycle_role is LifecycleRole.CHAIN_FOLLOWER


def test_failed_payment_retry_is_reserved():
    ledger = ledger_for(
        event("fail", status="failed", event_type="debt_payment"),
        event("retry", status="scheduled", event_type="debt_payment", linked_event_id="fail", settlement_date="2024-01-14"),
    )
    assert ledger["fail"].treatment is CashTreatment.IGNORED_FAILED
    assert ledger["retry"].treatment is CashTreatment.RESERVED_DEBIT
    assert ledger["retry"].signed_amount_home == Decimal("-100")


def test_pending_refund_is_not_counted():
    ledger = ledger_for(
        event("buy"),
        event("refund", event_type="refund", direction="credit", status="pending", linked_event_id="buy"),
    )
    assert ledger["refund"].treatment is CashTreatment.IGNORED_PENDING_CREDIT


def test_possible_duplicate_charge_is_flagged():
    ledger = ledger_for(event("orig"), event("dup", status="pending", linked_event_id="orig"))
    assert ledger["dup"].treatment is CashTreatment.DUPLICATE_SUSPECT


def test_unrealized_valuation_is_non_cash():
    ledger = ledger_for(
        event("val", event_type="investment_valuation", direction="non_cash", status="unrealized", settlement_date="")
    )
    assert ledger["val"].treatment is CashTreatment.NON_CASH
    assert ledger["val"].signed_amount_home is None


def test_scheduled_foreign_salary_converts_on_settlement_date():
    rate = ExchangeRate(rate_date="2024-01-15", from_currency="USD", to_currency="INR", rate="83")
    ledger = ledger_for(
        event(
            "pay",
            event_type="income",
            direction="credit",
            category="salary",
            amount="1000",
            currency="USD",
            status="scheduled",
            event_date="2024-01-15",
            settlement_date="2024-01-15",
        ),
        rates=(rate,),
    )
    assert ledger["pay"].treatment is CashTreatment.SCHEDULED_CREDIT
    assert ledger["pay"].amount_home == Decimal("83000")


def test_blank_amount_is_flagged_not_zero():
    ledger = ledger_for(event("bill", amount="", status="pending"))
    assert ledger["bill"].amount_home is None
    assert ledger["bill"].needs_image_amount


def test_real_dataset_lifecycle_examples(ledger):
    entries = {entry.event_id: entry for user_entries in ledger.values() for entry in user_entries}
    assert entries["event_185"].treatment is CashTreatment.RESERVED_DEBIT
    assert entries["event_185"].amount_home == Decimal("1651100")
    assert entries["event_103"].treatment is CashTreatment.SCHEDULED_CREDIT
    assert entries["event_1785"].treatment is CashTreatment.IGNORED_PENDING_CREDIT
    assert entries["event_5169"].treatment is CashTreatment.RESERVED_DEBIT
    assert entries["event_12709"].treatment is CashTreatment.DUPLICATE_SUSPECT
    assert entries["event_1856"].treatment is CashTreatment.NON_CASH
    assert entries["event_1442"].needs_image_amount
