"""Guardrail G4: every output row must satisfy the submission contract, independently of how it was produced."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ..output.writer import OutputRow
from ..schemas.domain import FinancialEvent, PaymentOption, Profile, PurchaseRequest
from ..schemas.enums import AffordabilityStatus, Flexibility, PaymentMethod, Severity
from ..schemas.obs import GuardrailViolation

STOPPABLE = {Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE}
REDUCIBLE = {Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE}
AMOUNT_TOLERANCE = Decimal("0.01")


class ContractParseError(ValueError):
    pass


def parse_plan(text: str) -> list[tuple[date, Decimal]]:
    if text == "none":
        return []
    payments = []
    for part in text.split("|"):
        try:
            day, amount = part.split(":")
            payments.append((date.fromisoformat(day), Decimal(amount)))
        except (ValueError, InvalidOperation) as exc:
            raise ContractParseError(f"bad payment entry {part!r}") from exc
    return payments


def parse_changes(text: str) -> list[tuple[str, str, Decimal | None]]:
    if text == "none":
        return []
    changes = []
    for part in text.split("|"):
        pieces = part.split(":")
        try:
            if pieces[0] == "stop" and len(pieces) == 2:
                changes.append(("stop", pieces[1], None))
            elif pieces[0] == "reduce_to" and len(pieces) == 3:
                changes.append(("reduce_to", pieces[1], Decimal(pieces[2])))
            else:
                raise ContractParseError(f"bad spending change {part!r}")
        except InvalidOperation as exc:
            raise ContractParseError(f"bad spending change amount {part!r}") from exc
    return changes


def check_output_row(
    row: OutputRow,
    request: PurchaseRequest,
    profile: Profile,
    options: Sequence[PaymentOption],
    events_by_id: Mapping[str, FinancialEvent],
) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []

    def fail(code: str, message: str, **details: Any) -> None:
        violations.append(
            GuardrailViolation(
                layer="G4", code=code, severity=Severity.ERROR, message=message, request_id=row.request_id, details=details
            )
        )

    amount = request.requested_amount
    accepted = set(profile.payment_methods_user_will_consider)
    status, method = row.affordability_status, row.recommended_payment_method

    try:
        safe = Decimal(row.amount_safe_to_pay)
        plan = parse_plan(row.payment_plan)
        changes = parse_changes(row.spending_changes_needed)
        earliest = date.fromisoformat(row.earliest_date_for_full_payment) if row.earliest_date_for_full_payment else None
    except (ContractParseError, InvalidOperation, ValueError) as exc:
        fail("unparseable_field", str(exc))
        return violations

    if not Decimal(0) <= safe <= amount:
        fail("safe_amount_out_of_bounds", f"amount_safe_to_pay {safe} not in [0, {amount}]")
    if [day for day, _ in plan] != sorted(day for day, _ in plan):
        fail("plan_not_chronological", "payment_plan dates are not in order")
    if not row.decision_explanation.strip():
        fail("empty_explanation", "decision_explanation is empty")

    pairing = {
        PaymentMethod.FULL_PAYMENT: {AffordabilityStatus.AFFORDABLE_NOW, AffordabilityStatus.AFFORDABLE_WITH_PLAN},
        PaymentMethod.PARTIAL_PAYMENT: {AffordabilityStatus.AFFORDABLE_WITH_PLAN},
        PaymentMethod.INSTALLMENTS: {AffordabilityStatus.AFFORDABLE_WITH_PLAN},
        PaymentMethod.WAIT: {AffordabilityStatus.AFFORDABLE_LATER},
        PaymentMethod.NOT_RECOMMENDED: {AffordabilityStatus.NOT_AFFORDABLE},
    }
    if status not in pairing[method]:
        fail("status_method_mismatch", f"{status} cannot pair with {method}")
    if method in (PaymentMethod.FULL_PAYMENT, PaymentMethod.PARTIAL_PAYMENT, PaymentMethod.INSTALLMENTS) and method not in accepted:
        fail("method_not_accepted", f"user does not consider {method}")

    if method is PaymentMethod.NOT_RECOMMENDED:
        if plan:
            fail("plan_for_not_recommended", "not_recommended must have payment_plan none")
        if changes:
            fail("changes_for_not_recommended", "not_recommended must have no spending changes")
    elif not plan:
        fail("missing_plan", f"{method} requires a payment plan")

    if status is AffordabilityStatus.AFFORDABLE_NOW:
        if earliest != request.request_date:
            fail("affordable_now_date", "affordable_now requires earliest_date_for_full_payment = request_date")
        if changes:
            fail("affordable_now_with_changes", "affordable_now cannot need spending changes")
    if earliest is not None and earliest < request.request_date:
        fail("earliest_before_request", "earliest date precedes request_date")

    total = sum((value for _, value in plan), Decimal(0))
    if method is PaymentMethod.FULL_PAYMENT and plan:
        if len(plan) != 1 or abs(plan[0][1] - amount) > AMOUNT_TOLERANCE or plan[0][0] != request.request_date:
            fail("full_payment_shape", "full_payment must be one payment of requested_amount on request_date")
    if method is PaymentMethod.WAIT and plan:
        if PaymentMethod.FULL_PAYMENT not in accepted:
            fail("wait_without_full_payment", "wait requires the user to accept full_payment")
        if len(plan) != 1 or abs(plan[0][1] - amount) > AMOUNT_TOLERANCE or plan[0][0] != earliest:
            fail("wait_shape", "wait must be one full payment on earliest_date_for_full_payment")
    if method is PaymentMethod.PARTIAL_PAYMENT and plan:
        if not request.allows_partial_payment:
            fail("partial_not_allowed", "request does not allow partial payment")
        if not Decimal(0) < safe < amount:
            fail("partial_safe_amount", "partial payment needs 0 < amount_safe_to_pay < requested_amount")
        if len(plan) != 2:
            fail("partial_shape", "partial payment must have exactly two payments")
        else:
            (day_one, first), (day_two, second) = plan
            if day_one != request.request_date or abs(first - safe) > AMOUNT_TOLERANCE:
                fail("partial_first_payment", "first partial payment must be amount_safe_to_pay on request_date")
            if day_two != earliest or earliest is None or earliest > request.desired_completion_date:
                fail("partial_second_payment", "second payment must fall on earliest date, on or before the deadline")
            if abs(total - amount) > AMOUNT_TOLERANCE:
                fail("partial_total", f"partial payments sum to {total}, not {amount}")
    if method is PaymentMethod.INSTALLMENTS and plan:
        schedules = {
            option.payment_option_id: [(day, value) for day, value in option.schedule()]
            for option in options
            if option.payment_method is PaymentMethod.INSTALLMENTS
        }
        matching = [option_id for option_id, schedule in schedules.items() if schedule == plan]
        if not matching:
            fail("installments_no_matching_option", "installment plan does not exactly match a supplied option")
        if profile.max_installment_months is None or len(plan) > profile.max_installment_months:
            fail("installments_exceed_limit", "installment plan exceeds max_installment_months")

    if len(changes) > 3:
        fail("too_many_changes", "at most three spending changes are allowed")
    seen_events: set[str] = set()
    protected = set(profile.expense_categories_to_protect)
    for action, event_id, new_amount in changes:
        if event_id in seen_events:
            fail("event_changed_twice", f"{event_id} is both stopped and reduced or repeated")
        seen_events.add(event_id)
        event = events_by_id.get(event_id)
        if event is None or event.user_id != request.user_id:
            fail("unknown_change_event", f"{event_id} is not an event of {request.user_id}")
            continue
        if event.category in protected:
            fail("change_protected_category", f"{event_id} is in protected category {event.category}")
        if action == "stop":
            if event.flexibility not in STOPPABLE or event.category not in profile.expense_categories_user_is_willing_to_stop:
                fail("stop_not_permitted", f"{event_id} may not be stopped")
        else:
            if event.flexibility not in REDUCIBLE or event.category not in profile.expense_categories_user_is_willing_to_reduce:
                fail("reduce_not_permitted", f"{event_id} may not be reduced")
            if event.minimum_allowed_amount is not None and new_amount < event.minimum_allowed_amount:
                fail("reduce_below_minimum", f"{event_id} reduced below minimum_allowed_amount")
    return violations
