"""Pydantic domain models for dataset rows and derived ledger entries."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator, model_validator

from .enums import (
    AffordabilityStatus,
    CashTreatment,
    Currency,
    Direction,
    EventStatus,
    EventType,
    Flexibility,
    FxMethod,
    LifecycleRole,
    PaymentMethod,
    RequestType,
    SourceAuthority,
)


def _split_pipe(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split("|") if part.strip()]
    return value


PipeList = Annotated[list[str], BeforeValidator(_split_pipe)]
MethodList = Annotated[list[PaymentMethod], BeforeValidator(_split_pipe)]


class CsvRow(BaseModel):
    """A row read from a dataset CSV. Blank cells become None, never zero."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _blank_cells_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: None if isinstance(value, str) and not value.strip() else value
                for key, value in data.items()
            }
        return data


class Profile(CsvRow):
    user_id: str
    home_currency: Currency
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: PipeList
    expense_categories_to_protect: PipeList
    expense_categories_user_is_willing_to_reduce: PipeList
    expense_categories_user_is_willing_to_stop: PipeList
    payment_methods_user_will_consider: MethodList
    max_installment_months: int | None = None


class FinancialEvent(CsvRow):
    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: str
    direction: Direction
    amount: Decimal | None = None
    currency: Currency
    event_date: date
    settlement_date: date | None = None
    status: EventStatus
    linked_event_id: str | None = None
    flexibility: Flexibility
    minimum_allowed_amount: Decimal | None = None

    @field_validator("amount", "minimum_allowed_amount")
    @classmethod
    def _non_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("amounts must be non-negative; direction carries the sign")
        return value

    @property
    def cash_date(self) -> date:
        return self.settlement_date or self.event_date


class ExchangeRate(CsvRow):
    rate_date: date
    from_currency: Currency
    to_currency: Currency
    rate: Decimal

    @field_validator("rate")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("exchange rate must be positive")
        return value


class PurchaseRequest(CsvRow):
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


class ExpectedDecision(CsvRow):
    """Completed output columns from sample_requests.csv (used only by the eval harness)."""

    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: date | None = None
    spending_changes_needed: str
    decision_explanation: str


class PaymentOption(CsvRow):
    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None = None
    financing_fee: Decimal
    total_payable_amount: Decimal

    @model_validator(mode="after")
    def _schedule_is_complete(self) -> PaymentOption:
        if self.number_of_payments < 1:
            raise ValueError("number_of_payments must be at least 1")
        if self.number_of_payments > 1 and not self.payment_frequency_days:
            raise ValueError("options with several payments need payment_frequency_days")
        return self

    def schedule(self) -> list[tuple[date, Decimal]]:
        step = timedelta(days=self.payment_frequency_days or 0)
        return [(self.first_payment_date + step * index, self.payment_amount) for index in range(self.number_of_payments)]


class Message(CsvRow):
    message_id: str
    user_id: str
    request_id: str | None = None
    related_event_id: str | None = None
    sent_at: datetime
    source_type: SourceAuthority
    message_text: str


class ImageRef(CsvRow):
    image_id: str
    user_id: str
    request_id: str | None = None
    related_event_id: str | None = None

    def path(self, dataset_dir: Path) -> Path:
        return dataset_dir / "media" / "images" / f"{self.image_id}.png"


class FxConversion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rate: Decimal
    method: FxMethod
    rate_date: date
    path: tuple[Currency, ...]


class LedgerEntry(BaseModel):
    """One financial event with its cash treatment and home-currency amount resolved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    user_id: str
    event_type: EventType
    category: str
    description: str
    direction: Direction
    status: EventStatus
    flexibility: Flexibility
    event_date: date
    cash_date: date
    treatment: CashTreatment
    lifecycle_role: LifecycleRole
    linked_event_id: str | None
    original_amount: Decimal | None
    original_currency: Currency
    amount_home: Decimal | None
    minimum_allowed_amount_home: Decimal | None
    fx: FxConversion | None
    needs_image_amount: bool
    notes: tuple[str, ...] = ()

    @property
    def signed_amount_home(self) -> Decimal | None:
        if self.amount_home is None or self.direction is Direction.NON_CASH:
            return None
        return self.amount_home if self.direction is Direction.CREDIT else -self.amount_home
