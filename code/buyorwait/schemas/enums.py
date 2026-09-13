"""Enumerations shared by dataset rows, engine results and LLM schemas."""

from enum import StrEnum


class Currency(StrEnum):
    INR = "INR"
    ZAR = "ZAR"
    IDR = "IDR"
    USD = "USD"
    EUR = "EUR"


class EventType(StrEnum):
    EXPENSE = "expense"
    SUBSCRIPTION = "subscription"
    INCOME = "income"
    DEBT_PAYMENT = "debt_payment"
    INVESTMENT_PURCHASE = "investment_purchase"
    REFUND = "refund"
    INVESTMENT_VALUATION = "investment_valuation"
    INVESTMENT_SALE = "investment_sale"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class EventStatus(StrEnum):
    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNREALIZED = "unrealized"


class Flexibility(StrEnum):
    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"


class RequestType(StrEnum):
    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


class PaymentMethod(StrEnum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class AffordabilityStatus(StrEnum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class SourceAuthority(StrEnum):
    """`source_type` of a message: who is asserting the fact."""

    EMPLOYER = "employer"
    SERVICE_PROVIDER = "service_provider"
    FINANCIAL_SERVICE = "financial_service"
    BANK = "bank"
    MERCHANT = "merchant"


class CashTreatment(StrEnum):
    """How a financial event affects cash in the forecast."""

    SETTLED = "settled"  # already reflected in current_available_balance; history only
    RESERVED_DEBIT = "reserved_debit"  # pending/scheduled outflow, reserved on its cash date
    SCHEDULED_CREDIT = "scheduled_credit"  # confirmed future inflow, e.g. next confirmed salary
    IGNORED_PENDING_CREDIT = "ignored_pending_credit"
    DUPLICATE_SUSPECT = "duplicate_suspect"  # pending debit mirroring an already-settled debit
    IGNORED_FAILED = "ignored_failed"
    IGNORED_CANCELLED = "ignored_cancelled"
    NON_CASH = "non_cash"


class LifecycleRole(StrEnum):
    STANDALONE = "standalone"
    CHAIN_ORIGIN = "chain_origin"
    CHAIN_FOLLOWER = "chain_follower"


class FxMethod(StrEnum):
    IDENTITY = "identity"
    DIRECT = "direct"
    INVERSE = "inverse"
    CROSS = "cross"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class SpanStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
