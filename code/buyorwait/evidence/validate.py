"""Guardrails G2 (source authority, injection) and G3 (verbatim citations, grounded amounts/dates, confidence)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..guardrails.injection import scan_for_injection
from ..ingest.fx import FxRateMissing, FxTable
from ..schemas.domain import FinancialEvent, ImageRef, Message, Profile
from ..schemas.enums import Currency, Direction, Severity, SourceAuthority
from ..schemas.evidence import (
    AcceptedFact,
    ConfidenceLabel,
    EvidenceFact,
    EvidenceKind,
    EvidenceReview,
    ImageExtraction,
    IncomeSource,
    MessageExtraction,
)
from ..schemas.obs import GuardrailViolation

MIN_CONFIDENCE = 0.6
LABEL_RANGES = {
    ConfidenceLabel.HIGH: (0.8, 1.0),
    ConfidenceLabel.MEDIUM: (0.5, 0.8),
    ConfidenceLabel.LOW: (0.0, 0.5),
}

K = EvidenceKind
ALWAYS_ALLOWED = {K.NO_FINANCIAL_EFFECT, K.SUSPICIOUS_REQUEST}
AUTHORITY: dict[SourceAuthority, set[EvidenceKind]] = {
    SourceAuthority.EMPLOYER: {
        K.SALARY_AMOUNT_CHANGE, K.SALARY_NEXT_PAYMENT_AMOUNT, K.SALARY_DATE_CHANGE, K.SALARY_CONFIRMED,
        K.INCOME_ENDED, K.INCOME_REMAINING_TOTAL, K.INCOME_NOT_CONFIRMED, K.ONE_TIME_INCOME_CONFIRMED,
        K.CREDIT_ALREADY_SETTLED, K.NEW_RECURRING_EXPENSE,
    },
    SourceAuthority.SERVICE_PROVIDER: {
        K.INCOME_NOT_CONFIRMED, K.ONE_TIME_INCOME_CONFIRMED, K.INCOME_ENDED, K.EXPENSE_PERCENT_CHANGE,
        K.NEW_RECURRING_EXPENSE, K.BILL_STILL_OUTSTANDING, K.CREDIT_ALREADY_SETTLED,
    },
    SourceAuthority.BANK: {K.INTERNAL_TRANSFER, K.BILL_STILL_OUTSTANDING},
    SourceAuthority.MERCHANT: {K.REFUND_NOT_SETTLED, K.BILL_STILL_OUTSTANDING, K.CREDIT_ALREADY_SETTLED},
    SourceAuthority.FINANCIAL_SERVICE: {
        K.INCOME_NOT_CONFIRMED, K.CREDIT_ALREADY_SETTLED, K.INVESTMENT_VALUE_NON_CASH,
    },
}
REQUIRED_FIELDS: dict[EvidenceKind, tuple[str, ...]] = {
    K.SALARY_AMOUNT_CHANGE: ("amount",),
    K.SALARY_NEXT_PAYMENT_AMOUNT: ("amount",),
    K.SALARY_DATE_CHANGE: ("effective_date",),
    K.SALARY_CONFIRMED: ("amount", "effective_date"),
    K.INCOME_REMAINING_TOTAL: ("amount",),
    K.ONE_TIME_INCOME_CONFIRMED: ("amount",),
    K.EXPENSE_PERCENT_CHANGE: ("percent", "category"),
}

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LONG_DATE = re.compile(r"(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})")


def normalize_text(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("‘", "'").split()).lower()


def numbers_in(text: str) -> set[Decimal]:
    values = set()
    for token in _NUMBER.findall(text):
        try:
            values.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return values


def dates_in(text: str) -> set[date]:
    found = set()
    for token in _ISO_DATE.findall(text):
        try:
            found.add(date.fromisoformat(token))
        except ValueError:
            continue
    for day, month, year in _LONG_DATE.findall(text):
        found.add(datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date())
    return found


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


class _Checker:
    def __init__(self, source_id: str, request_id: str | None) -> None:
        self.source_id = source_id
        self.request_id = request_id
        self.violations: list[GuardrailViolation] = []

    def add(self, layer: str, code: str, message: str, severity: Severity = Severity.ERROR, **details: Any) -> None:
        self.violations.append(
            GuardrailViolation(
                layer=layer,
                code=code,
                severity=severity,
                message=message,
                entity_id=self.source_id,
                request_id=self.request_id,
                details=details,
            )
        )


def _check_confidence(check: _Checker, label: ConfidenceLabel, score: float, where: str) -> bool:
    low, high = LABEL_RANGES[label]
    if not 0.0 <= score <= 1.0:
        check.add("G3", "confidence_out_of_range", f"{where}: confidence_score {score} outside [0, 1]")
        return False
    if not low - 0.05 <= score <= high + 0.05:
        check.add("G3", "confidence_label_mismatch", f"{where}: score {score} does not match label {label}")
        return False
    if score < MIN_CONFIDENCE:
        check.add("G3", "low_confidence", f"{where}: confidence {score} below {MIN_CONFIDENCE}", Severity.WARNING)
        return False
    return True


def _to_home(
    check: _Checker, amount: Decimal | None, currency: Currency | None, home: Currency, on: date, fx: FxTable, where: str
) -> tuple[Decimal | None, bool]:
    if amount is None or currency is None or currency == home:
        return amount, True
    try:
        return amount * fx.rate(currency, home, on).rate, True
    except FxRateMissing as exc:
        check.add("G3", "fx_missing", f"{where}: {exc}")
        return None, False


def review_message(
    extraction: MessageExtraction, message: Message, profile: Profile, fx: FxTable
) -> EvidenceReview:
    check = _Checker(message.message_id, message.request_id)
    text = message.message_text
    normalized = normalize_text(text)
    numbers, dates = numbers_in(text), dates_in(text)
    sent_on = message.sent_at.date()

    flagged = scan_for_injection(text)
    if flagged or extraction.injection_detected:
        check.add("G2", "injection_suspected", "untrusted message contains instruction-like text", Severity.WARNING,
                  scanner=flagged, model_quote=extraction.injection_quote)

    accepted: list[AcceptedFact] = []
    allowed = AUTHORITY.get(message.source_type, set()) | ALWAYS_ALLOWED
    for index, fact in enumerate(extraction.facts):
        where = f"{message.message_id}#{index} {fact.kind}"
        before = len([v for v in check.violations if v.severity is Severity.ERROR])
        if fact.kind not in allowed:
            check.add("G2", "kind_not_allowed_for_source", f"{where}: {message.source_type} may not assert {fact.kind}")
        if not fact.citations:
            check.add("G3", "missing_citation", f"{where}: no citation")
        for citation in fact.citations:
            if normalize_text(citation.quote) not in normalized:
                check.add("G3", "quote_not_in_source", f"{where}: quote is not verbatim", quote=citation.quote)

        amount = _parse_decimal(fact.amount)
        if fact.amount is not None and (amount is None or amount <= 0):
            check.add("G3", "invalid_amount", f"{where}: amount {fact.amount!r} is not a positive number")
        elif amount is not None and amount not in numbers:
            check.add("G3", "amount_not_in_source", f"{where}: amount {amount} does not appear in the message")
        percent = _parse_decimal(fact.percent)
        if fact.percent is not None and (percent is None or percent not in numbers):
            check.add("G3", "percent_not_in_source", f"{where}: percent {fact.percent!r} does not appear in the message")
        effective = None
        if fact.effective_date is not None:
            try:
                effective = date.fromisoformat(fact.effective_date)
            except ValueError:
                check.add("G3", "invalid_date", f"{where}: bad date {fact.effective_date!r}")
            else:
                if effective not in dates:
                    check.add("G3", "date_not_in_source", f"{where}: date {effective} does not appear in the message")
        for field in REQUIRED_FIELDS.get(fact.kind, ()):
            if getattr(fact, field) is None:
                check.add("G3", "missing_required_field", f"{where}: {field} is required for {fact.kind}")

        confident = _check_confidence(check, fact.confidence, fact.confidence_score, where)
        amount_home, converted = _to_home(check, amount, fact.currency, profile.home_currency, effective or sent_on, fx, where)
        errors_now = len([v for v in check.violations if v.severity is Severity.ERROR])
        if errors_now > before or not confident or not converted:
            continue
        accepted.append(
            AcceptedFact(
                fact_id=f"{message.message_id}#{index}",
                source_id=message.message_id,
                user_id=message.user_id,
                request_id=message.request_id,
                related_event_id=message.related_event_id,
                sent_on=sent_on,
                source_authority=message.source_type,
                kind=fact.kind,
                income_source=fact.income_source,
                original_amount=amount,
                currency=fact.currency,
                amount_home=amount_home,
                percent=percent,
                effective_date=effective,
                category=fact.category,
                confidence_score=fact.confidence_score,
                quotes=tuple(citation.quote for citation in fact.citations),
            )
        )
    return EvidenceReview(
        source_id=message.message_id,
        source_type="message",
        user_id=message.user_id,
        summary=extraction.english_summary,
        accepted=accepted,
        violations=check.violations,
        injection_detected=bool(flagged or extraction.injection_detected),
    )


def _read_amount(check: _Checker, read: ImageExtraction, label: str) -> Decimal | None:
    transcript = "\n".join(read.transcript_lines)
    if read.injection_detected:
        check.add("G2", "injection_suspected", f"{label}: image contains instruction-like text", Severity.WARNING,
                  model_quote=read.injection_quote)
    amount = _parse_decimal(read.amount_for_event)
    if amount is None or amount <= 0:
        check.add("G3", "image_no_amount", f"{label}: no usable amount", Severity.WARNING)
        return None
    if amount not in numbers_in(transcript):
        check.add("G3", "amount_not_in_transcript", f"{label}: {amount} not found in transcribed lines")
        return None
    normalized = normalize_text(transcript)
    if not read.citations or any(normalize_text(c.quote) not in normalized for c in read.citations):
        check.add("G3", "quote_not_in_source", f"{label}: citation is not a transcribed line")
        return None
    if not _check_confidence(check, read.confidence, read.confidence_score, label):
        return None
    return amount


def review_image(
    reads: Sequence[ImageExtraction], image: ImageRef, event: FinancialEvent, profile: Profile, fx: FxTable
) -> EvidenceReview:
    """Two independent reads must agree; if they disagree the financially safer amount is used and flagged."""
    check = _Checker(image.image_id, image.request_id)
    amounts = [amount for index, read in enumerate(reads) if (amount := _read_amount(check, read, f"{image.image_id} read {index}")) is not None]
    accepted: list[AcceptedFact] = []
    if amounts:
        if len(amounts) < len(reads):
            check.add("G3", "single_valid_read", f"{image.image_id}: only {len(amounts)} of {len(reads)} reads were usable", Severity.WARNING)
        if max(amounts) - min(amounts) > Decimal("0.01"):
            check.add("G3", "image_reads_disagree", f"{image.image_id}: reads disagree {amounts}; using the safer amount", Severity.WARNING)
        chosen = max(amounts) if event.direction is Direction.DEBIT else min(amounts)
        amount_home, converted = _to_home(check, chosen, event.currency, profile.home_currency, event.cash_date, fx, image.image_id)
        if converted:
            best = next(read for read in reads if _parse_decimal(read.amount_for_event) == chosen)
            accepted.append(
                AcceptedFact(
                    fact_id=f"{image.image_id}#amount",
                    source_id=image.image_id,
                    user_id=image.user_id,
                    request_id=image.request_id,
                    related_event_id=event.event_id,
                    sent_on=None,
                    source_authority=None,
                    kind=EvidenceKind.DOCUMENT_AMOUNT,
                    income_source=IncomeSource.NOT_APPLICABLE,
                    original_amount=chosen,
                    currency=event.currency,
                    amount_home=amount_home,
                    percent=None,
                    effective_date=None,
                    category=event.category,
                    confidence_score=min(read.confidence_score for read in reads),
                    quotes=tuple(citation.quote for citation in best.citations),
                )
            )
    return EvidenceReview(
        source_id=image.image_id,
        source_type="image",
        user_id=image.user_id,
        summary=f"{event.description}: {amounts}",
        accepted=accepted,
        violations=check.violations,
        injection_detected=any(read.injection_detected for read in reads),
    )
