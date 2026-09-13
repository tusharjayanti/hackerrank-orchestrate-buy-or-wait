"""Load dataset CSVs into validated Pydantic models. Invalid rows become G1 violations, not crashes."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..schemas.domain import (
    ExchangeRate,
    ExpectedDecision,
    FinancialEvent,
    ImageRef,
    Message,
    PaymentOption,
    Profile,
    PurchaseRequest,
)
from ..schemas.enums import Severity
from ..schemas.obs import GuardrailViolation
from .fx import FxTable

RowModel = TypeVar("RowModel", bound=BaseModel)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _validate_rows(
    path: Path, rows: list[dict[str, str]], model: type[RowModel], violations: list[GuardrailViolation]
) -> list[RowModel]:
    parsed: list[RowModel] = []
    for row_number, row in enumerate(rows, start=1):
        try:
            parsed.append(model.model_validate(row))
        except ValidationError as exc:
            violations.append(
                GuardrailViolation(
                    layer="G1",
                    code="csv_row_invalid",
                    severity=Severity.ERROR,
                    entity_id=next(iter(row.values()), None),
                    message=f"{path.name} row {row_number} failed {model.__name__} validation",
                    details={"errors": json.loads(exc.json(include_url=False))},
                )
            )
    return parsed


@dataclass
class Dataset:
    root: Path
    profiles: dict[str, Profile]
    events: list[FinancialEvent]
    rates: list[ExchangeRate]
    requests: list[PurchaseRequest]
    sample_requests: list[PurchaseRequest]
    sample_expected: dict[str, ExpectedDecision]
    payment_options: list[PaymentOption]
    messages: list[Message]
    images: list[ImageRef]
    template_request_ids: list[str]
    load_violations: list[GuardrailViolation]

    fx: FxTable = field(init=False)
    events_by_id: dict[str, FinancialEvent] = field(init=False)
    events_by_user: dict[str, list[FinancialEvent]] = field(init=False)
    options_by_request: dict[str, list[PaymentOption]] = field(init=False)
    messages_by_user: dict[str, list[Message]] = field(init=False)
    images_by_user: dict[str, list[ImageRef]] = field(init=False)
    images_by_event: dict[str, list[ImageRef]] = field(init=False)

    def __post_init__(self) -> None:
        self.fx = FxTable(self.rates)
        self.events_by_id = {event.event_id: event for event in self.events}
        self.events_by_user = _group(self.events, lambda event: event.user_id)
        self.options_by_request = _group(self.payment_options, lambda option: option.request_id)
        self.messages_by_user = _group(self.messages, lambda message: message.user_id)
        self.images_by_user = _group(self.images, lambda image: image.user_id)
        self.images_by_event = _group(
            [image for image in self.images if image.related_event_id], lambda image: image.related_event_id
        )

    @property
    def all_requests(self) -> list[PurchaseRequest]:
        return [*self.sample_requests, *self.requests]

    def image_path(self, image: ImageRef) -> Path:
        return image.path(self.root)


def _group(items, key) -> dict:
    grouped = defaultdict(list)
    for item in items:
        grouped[key(item)].append(item)
    return dict(grouped)


def load_dataset(root: Path) -> Dataset:
    violations: list[GuardrailViolation] = []

    def load(name: str, model: type[RowModel]) -> list[RowModel]:
        path = root / name
        return _validate_rows(path, _read_csv(path), model, violations)

    profiles = load("financial_profiles.csv", Profile)
    profiles_by_user: dict[str, Profile] = {}
    for profile in profiles:
        if profile.user_id in profiles_by_user:
            violations.append(
                GuardrailViolation(
                    layer="G1",
                    code="duplicate_id",
                    severity=Severity.ERROR,
                    entity_id=profile.user_id,
                    message="duplicate user_id in financial_profiles.csv",
                )
            )
        profiles_by_user[profile.user_id] = profile

    sample_path = root / "sample_requests.csv"
    sample_rows = _read_csv(sample_path)
    request_fields = set(PurchaseRequest.model_fields)
    expected_fields = set(ExpectedDecision.model_fields)
    sample_requests = _validate_rows(
        sample_path, [{k: v for k, v in row.items() if k in request_fields} for row in sample_rows], PurchaseRequest, violations
    )
    sample_expected = _validate_rows(
        sample_path, [{k: v for k, v in row.items() if k in expected_fields} for row in sample_rows], ExpectedDecision, violations
    )

    return Dataset(
        root=root,
        profiles=profiles_by_user,
        events=load("financial_events.csv", FinancialEvent),
        rates=load("exchange_rates.csv", ExchangeRate),
        requests=load("requests.csv", PurchaseRequest),
        sample_requests=sample_requests,
        sample_expected={expected.request_id: expected for expected in sample_expected},
        payment_options=load("request_payment_options.csv", PaymentOption),
        messages=load("messages.csv", Message),
        images=load("images.csv", ImageRef),
        template_request_ids=[row["request_id"] for row in _read_csv(root / "output.csv")],
        load_violations=violations,
    )
