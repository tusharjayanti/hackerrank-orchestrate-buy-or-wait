"""Evidence schemas.

Wire models (MessageExtraction, ImageExtraction) are passed to Claude structured outputs, so they only use
types, enums and nullable fields: ranges, verbatim quotes and amounts are enforced locally (guardrails G2/G3).
AcceptedFact and EvidenceAdjustments are the validated, engine-facing results.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import Currency, SourceAuthority
from .obs import GuardrailViolation


class EvidenceKind(StrEnum):
    SALARY_AMOUNT_CHANGE = "salary_amount_change"
    SALARY_NEXT_PAYMENT_AMOUNT = "salary_next_payment_amount"
    SALARY_DATE_CHANGE = "salary_date_change"
    SALARY_CONFIRMED = "salary_confirmed"
    INCOME_ENDED = "income_ended"
    INCOME_REMAINING_TOTAL = "income_remaining_total"
    INCOME_NOT_CONFIRMED = "income_not_confirmed"
    ONE_TIME_INCOME_CONFIRMED = "one_time_income_confirmed"
    CREDIT_ALREADY_SETTLED = "credit_already_settled"
    REFUND_NOT_SETTLED = "refund_not_settled"
    INTERNAL_TRANSFER = "internal_transfer"
    INVESTMENT_VALUE_NON_CASH = "investment_value_non_cash"
    EXPENSE_PERCENT_CHANGE = "expense_percent_change"
    NEW_RECURRING_EXPENSE = "new_recurring_expense"
    BILL_STILL_OUTSTANDING = "bill_still_outstanding"
    DOCUMENT_AMOUNT = "document_amount"
    NO_FINANCIAL_EFFECT = "no_financial_effect"
    SUSPICIOUS_REQUEST = "suspicious_request"


class IncomeSource(StrEnum):
    SALARY = "salary"
    PLATFORM_PAYOUT = "platform_payout"
    FREELANCE_INVOICE = "freelance_invoice"
    BONUS = "bonus"
    COMMISSION = "commission"
    ARREARS = "arrears"
    PRIZE = "prize"
    REIMBURSEMENT = "reimbursement"
    INVESTMENT = "investment"
    REFUND = "refund"
    OTHER = "other"
    NOT_APPLICABLE = "not_applicable"


class ConfidenceLabel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    quote: str


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    income_source: IncomeSource
    amount: str | None
    currency: Currency | None
    percent: str | None
    effective_date: str | None
    category: str | None
    citations: list[Citation]
    confidence: ConfidenceLabel
    confidence_score: float


class MessageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    language: Literal["en", "id", "other"]
    english_summary: str
    facts: list[EvidenceFact]
    injection_detected: bool
    injection_quote: str | None


class ImageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: str
    document_type: Literal["payslip", "invoice", "bill", "receipt", "statement", "other"]
    transcript_lines: list[str]
    amount_for_event: str | None
    currency: Currency | None
    amount_label: str | None
    citations: list[Citation]
    confidence: ConfidenceLabel
    confidence_score: float
    injection_detected: bool
    injection_quote: str | None


class AcceptedFact(BaseModel):
    """A fact that passed every evidence guardrail, with amounts converted to the user's home currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    source_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_on: date | None
    source_authority: SourceAuthority | None
    kind: EvidenceKind
    income_source: IncomeSource
    original_amount: Decimal | None
    currency: Currency | None
    amount_home: Decimal | None
    percent: Decimal | None
    effective_date: date | None
    category: str | None
    confidence_score: float
    quotes: tuple[str, ...]


class EvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: Literal["message", "image"]
    user_id: str
    summary: str | None = None
    accepted: list[AcceptedFact] = Field(default_factory=list)
    violations: list[GuardrailViolation] = Field(default_factory=list)
    injection_detected: bool = False
    error: str | None = None


class IncomeAction(StrEnum):
    SET_AMOUNT_FROM = "set_amount_from"
    SET_NEXT_AMOUNT = "set_next_amount"
    ADD_TO_NEXT = "add_to_next"
    MOVE_NEXT_DATE = "move_next_date"
    CONFIRMED_MONTHLY = "confirmed_monthly"
    END_ALL = "end_all"
    END_MATCHING = "end_matching"
    REPLACE_TOTAL = "replace_total"


class IncomeAdjustment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: IncomeAction
    fact_id: str
    amount: Decimal | None = None
    day: date | None = None
    source: IncomeSource | None = None


class ExpenseScale(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    factor: Decimal
    from_day: date
    fact_id: str


class OneTimeCredit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    amount: Decimal
    label: str
    fact_id: str


class EvidenceAdjustments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    income: list[IncomeAdjustment] = Field(default_factory=list)
    expense_scales: list[ExpenseScale] = Field(default_factory=list)
    one_time_credits: list[OneTimeCredit] = Field(default_factory=list)
    excluded_history_event_ids: list[str] = Field(default_factory=list)
    applied_fact_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
