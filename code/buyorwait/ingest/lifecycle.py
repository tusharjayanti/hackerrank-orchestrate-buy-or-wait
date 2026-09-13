"""Classify each financial event's cash effect and convert it to the user's home currency."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from ..schemas.domain import FinancialEvent, FxConversion, LedgerEntry, Profile
from ..schemas.enums import CashTreatment, Direction, EventStatus, LifecycleRole
from .fx import FxRateMissing, FxTable


def classify_event(event: FinancialEvent, events_by_id: Mapping[str, FinancialEvent]) -> CashTreatment:
    if event.direction is Direction.NON_CASH or event.status is EventStatus.UNREALIZED:
        return CashTreatment.NON_CASH
    if event.status is EventStatus.FAILED:
        return CashTreatment.IGNORED_FAILED
    if event.status is EventStatus.CANCELLED:
        return CashTreatment.IGNORED_CANCELLED
    if event.status is EventStatus.SETTLED:
        return CashTreatment.SETTLED
    # Pending or scheduled from here on.
    if event.direction is Direction.CREDIT:
        if event.status is EventStatus.SCHEDULED:
            return CashTreatment.SCHEDULED_CREDIT
        return CashTreatment.IGNORED_PENDING_CREDIT
    if _mirrors_settled_debit(event, events_by_id):
        return CashTreatment.DUPLICATE_SUSPECT
    return CashTreatment.RESERVED_DEBIT


def _mirrors_settled_debit(event: FinancialEvent, events_by_id: Mapping[str, FinancialEvent]) -> bool:
    """A pending debit linked to an already-settled debit of the same amount looks like a duplicate record."""
    origin = events_by_id.get(event.linked_event_id) if event.linked_event_id else None
    return (
        origin is not None
        and origin.status is EventStatus.SETTLED
        and origin.direction is Direction.DEBIT
        and origin.amount is not None
        and origin.amount == event.amount
    )


def _lifecycle_role(event: FinancialEvent, link_targets: set[str]) -> LifecycleRole:
    if event.linked_event_id:
        return LifecycleRole.CHAIN_FOLLOWER
    if event.event_id in link_targets:
        return LifecycleRole.CHAIN_ORIGIN
    return LifecycleRole.STANDALONE


def build_ledger(
    events: Iterable[FinancialEvent], profiles: Mapping[str, Profile], fx: FxTable
) -> dict[str, list[LedgerEntry]]:
    """Resolve every event into a LedgerEntry, grouped by user. Unknown users are reported by guardrail G1."""
    events = list(events)
    events_by_id = {event.event_id: event for event in events}
    link_targets = {event.linked_event_id for event in events if event.linked_event_id}
    ledger: dict[str, list[LedgerEntry]] = defaultdict(list)

    for event in events:
        profile = profiles.get(event.user_id)
        if profile is None:
            continue
        notes: list[str] = []
        conversion: FxConversion | None = None
        try:
            conversion = fx.rate(event.currency, profile.home_currency, event.cash_date)
        except FxRateMissing as exc:
            notes.append(f"fx_missing: {exc}")
        amount_home = event.amount * conversion.rate if conversion and event.amount is not None else None
        minimum_home = (
            event.minimum_allowed_amount * conversion.rate
            if conversion and event.minimum_allowed_amount is not None
            else None
        )
        if event.amount is None:
            notes.append("amount_blank: resolve from linked image")

        ledger[event.user_id].append(
            LedgerEntry(
                event_id=event.event_id,
                user_id=event.user_id,
                event_type=event.event_type,
                category=event.category,
                description=event.description,
                direction=event.direction,
                status=event.status,
                flexibility=event.flexibility,
                event_date=event.event_date,
                cash_date=event.cash_date,
                treatment=classify_event(event, events_by_id),
                lifecycle_role=_lifecycle_role(event, link_targets),
                linked_event_id=event.linked_event_id,
                original_amount=event.amount,
                original_currency=event.currency,
                amount_home=amount_home,
                minimum_allowed_amount_home=minimum_home,
                fx=conversion,
                needs_image_amount=event.amount is None,
                notes=tuple(notes),
            )
        )
    return dict(ledger)
