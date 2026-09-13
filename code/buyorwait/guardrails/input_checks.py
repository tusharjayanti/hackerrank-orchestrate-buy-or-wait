"""Guardrail G1: referential integrity and resolvability of the loaded dataset."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from ..ingest.fx import FxRateMissing
from ..ingest.loaders import Dataset
from ..schemas.enums import FxMethod, PaymentMethod, Severity
from ..schemas.obs import GuardrailViolation


class _Collector:
    def __init__(self, initial: Iterable[GuardrailViolation]) -> None:
        self.violations = list(initial)

    def add(self, code: str, severity: Severity, message: str, entity_id: str | None = None, **details: Any) -> None:
        self.violations.append(
            GuardrailViolation(
                layer="G1", code=code, severity=severity, message=message, entity_id=entity_id, details=details
            )
        )


def check_dataset(ds: Dataset) -> list[GuardrailViolation]:
    out = _Collector(ds.load_violations)
    _check_duplicate_ids(ds, out)
    _check_events(ds, out)
    _check_requests_and_options(ds, out)
    _check_messages_and_images(ds, out)
    _check_exchange_rates(ds, out)
    return out.violations


def _check_duplicate_ids(ds: Dataset, out: _Collector) -> None:
    id_sets = {
        "event_id": [event.event_id for event in ds.events],
        "request_id": [request.request_id for request in ds.all_requests],
        "payment_option_id": [option.payment_option_id for option in ds.payment_options],
        "message_id": [message.message_id for message in ds.messages],
        "image_id": [image.image_id for image in ds.images],
    }
    for id_name, ids in id_sets.items():
        for value, count in Counter(ids).items():
            if count > 1:
                out.add("duplicate_id", Severity.ERROR, f"{id_name} appears {count} times", value)


def _check_events(ds: Dataset, out: _Collector) -> None:
    for event in ds.events:
        profile = ds.profiles.get(event.user_id)
        if profile is None:
            out.add("unknown_user", Severity.ERROR, "event references a user with no profile", event.event_id)
            continue

        if event.linked_event_id:
            origin = ds.events_by_id.get(event.linked_event_id)
            if origin is None:
                out.add("dangling_link", Severity.ERROR, "linked_event_id does not exist", event.event_id)
            elif origin.user_id != event.user_id:
                out.add("cross_user_link", Severity.ERROR, "linked event belongs to another user", event.event_id)
            elif origin.event_date > event.event_date:
                out.add("link_not_earlier", Severity.WARNING, "linked event is dated after its follower", event.event_id)

        if event.amount is None:
            images = [image for image in ds.images_by_event.get(event.event_id, []) if ds.image_path(image).exists()]
            if not images:
                out.add(
                    "blank_amount_unresolvable",
                    Severity.ERROR,
                    "blank amount has no linked image file to extract it from",
                    event.event_id,
                )

        if event.currency != profile.home_currency:
            try:
                conversion = ds.fx.rate(event.currency, profile.home_currency, event.cash_date)
            except FxRateMissing as exc:
                out.add("fx_rate_missing", Severity.ERROR, str(exc), event.event_id)
            else:
                if conversion.method is not FxMethod.DIRECT:
                    out.add(
                        "fx_indirect_rate",
                        Severity.WARNING,
                        f"converted with {conversion.method} rate via {'->'.join(conversion.path)}",
                        event.event_id,
                    )


def _check_requests_and_options(ds: Dataset, out: _Collector) -> None:
    request_ids = {request.request_id for request in ds.all_requests}
    for request in ds.all_requests:
        if request.user_id not in ds.profiles:
            out.add("unknown_user", Severity.ERROR, "request references a user with no profile", request.request_id)
        if request.requested_amount <= 0:
            out.add("non_positive_request", Severity.ERROR, "requested_amount must be positive", request.request_id)
        if request.desired_completion_date < request.request_date:
            out.add("deadline_before_request", Severity.WARNING, "deadline precedes request date", request.request_id)
        option_count = len(ds.options_by_request.get(request.request_id, []))
        if not 2 <= option_count <= 4:
            out.add(
                "option_count_out_of_range",
                Severity.WARNING,
                f"request has {option_count} payment options (expected 2-4)",
                request.request_id,
            )

    for option in ds.payment_options:
        if option.request_id not in request_ids:
            out.add("unknown_request", Severity.ERROR, "payment option references unknown request", option.payment_option_id)
        if option.payment_method not in (PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS):
            out.add(
                "unexpected_option_method",
                Severity.WARNING,
                f"payment option method {option.payment_method} is not full_payment/installments",
                option.payment_option_id,
            )
        scheduled_total = option.payment_amount * option.number_of_payments
        tolerance = Decimal("0.01") * option.number_of_payments
        if abs(scheduled_total - option.total_payable_amount) > tolerance:
            out.add(
                "option_total_mismatch",
                Severity.WARNING,
                f"payments sum to {scheduled_total} but total_payable_amount is {option.total_payable_amount}",
                option.payment_option_id,
            )

    template_ids = ds.template_request_ids
    eval_ids = [request.request_id for request in ds.requests]
    if template_ids != eval_ids:
        out.add(
            "template_mismatch",
            Severity.ERROR,
            "dataset/output.csv request_ids differ from requests.csv",
            None,
            missing=sorted(set(eval_ids) - set(template_ids)),
            extra=sorted(set(template_ids) - set(eval_ids)),
        )
    sample_ids = {request.request_id for request in ds.sample_requests}
    if sample_ids != set(ds.sample_expected):
        out.add("sample_labels_mismatch", Severity.ERROR, "sample requests and sample labels differ", None)


def _check_messages_and_images(ds: Dataset, out: _Collector) -> None:
    requests_by_id = {request.request_id: request for request in ds.all_requests}

    def check_links(entity_id: str, user_id: str, request_id: str | None, event_id: str | None) -> None:
        if user_id not in ds.profiles:
            out.add("unknown_user", Severity.ERROR, "references a user with no profile", entity_id)
        if request_id:
            request = requests_by_id.get(request_id)
            if request is None:
                out.add("unknown_request", Severity.ERROR, "references unknown request", entity_id)
            elif request.user_id != user_id:
                out.add("cross_user_request", Severity.ERROR, "request belongs to another user", entity_id)
        if event_id:
            event = ds.events_by_id.get(event_id)
            if event is None:
                out.add("unknown_event", Severity.ERROR, "related_event_id does not exist", entity_id)
            elif event.user_id != user_id:
                out.add("cross_user_event", Severity.ERROR, "related event belongs to another user", entity_id)

    for message in ds.messages:
        check_links(message.message_id, message.user_id, message.request_id, message.related_event_id)
    for image in ds.images:
        check_links(image.image_id, image.user_id, image.request_id, image.related_event_id)
        if not ds.image_path(image).exists():
            out.add("image_file_missing", Severity.WARNING, f"{ds.image_path(image).name} not found", image.image_id)


def _check_exchange_rates(ds: Dataset, out: _Collector) -> None:
    seen: dict[tuple, Decimal] = {}
    for rate in ds.rates:
        key = (rate.rate_date, rate.from_currency, rate.to_currency)
        if key in seen and seen[key] != rate.rate:
            out.add("conflicting_rate", Severity.ERROR, f"conflicting rates for {key}", None)
        seen[key] = rate.rate
