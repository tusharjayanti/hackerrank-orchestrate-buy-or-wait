"""S4 evidence gold set: rule-based labels for the templated messages, hand labels for images, scoring of extracted facts."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..schemas.domain import Message
from ..schemas.evidence import AcceptedFact, EvidenceKind, EvidenceReview, IncomeSource

K = EvidenceKind
S = IncomeSource
FORECAST_KINDS = {
    K.SALARY_AMOUNT_CHANGE,
    K.SALARY_NEXT_PAYMENT_AMOUNT,
    K.SALARY_DATE_CHANGE,
    K.SALARY_CONFIRMED,
    K.INCOME_ENDED,
    K.INCOME_REMAINING_TOTAL,
    K.INCOME_NOT_CONFIRMED,
    K.ONE_TIME_INCOME_CONFIRMED,
    K.EXPENSE_PERCENT_CHANGE,
    K.INTERNAL_TRANSFER,
}
_AMOUNT = r"\s*(?:IDR|INR|EUR|USD|ZAR)\s?(?P<amount>\d[\d,]*(?:\.\d+)?)"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class GoldFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    income_source: IncomeSource = IncomeSource.NOT_APPLICABLE
    amount: Decimal | None = None
    effective_date: date | None = None
    percent: Decimal | None = None
    category: str | None = None


class MessageGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    source_type: str
    families: list[str]
    expected: list[GoldFact]
    note: str | None = None


class ImageGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: str
    related_event_id: str
    expected_amount: Decimal | None
    alternatives: list[Decimal] = Field(default_factory=list)
    label: str
    note: str | None = None


class KindScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float


class Disagreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    expected: str
    actual: str
    note: str | None = None


class EvidenceGoldReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: int
    images: int
    unmatched_messages: list[str]
    kind_scores: list[KindScore]
    forecast_precision: float
    forecast_recall: float
    amount_match_rate: float
    date_match_rate: float
    image_accuracy_strict: float
    image_accuracy_lenient: float
    disagreements: list[Disagreement]


def _amount_after(text: str, lead: str) -> Decimal | None:
    match = re.search(lead + _AMOUNT, text, re.IGNORECASE)
    return Decimal(match.group("amount").replace(",", "")) if match else None


def _first_date(text: str) -> date | None:
    match = _DATE.search(text)
    return date.fromisoformat(match.group(0)) if match else None


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def label_message(message: Message) -> MessageGold:
    """Deterministic labels for the message families in messages.csv (English and Indonesian variants)."""
    text = message.message_text
    facts: list[GoldFact] = []
    families: list[str] = []
    note = None

    def add(family: str, *new: GoldFact) -> None:
        families.append(family)
        facts.extend(new)

    lead = r"(?:monthly salary has increased to|Gaji bulanan Anda naik menjadi)"
    if _has(text, lead):
        add("salary_increase", GoldFact(kind=K.SALARY_AMOUNT_CHANGE, income_source=S.SALARY, amount=_amount_after(text, lead), effective_date=_first_date(text)))
    lead = r"(?:confirmed base salary is|Gaji pokok yang dikonfirmasi adalah)"
    if _has(text, lead):
        add(
            "base_salary_commission_pending",
            GoldFact(kind=K.SALARY_AMOUNT_CHANGE, income_source=S.SALARY, amount=_amount_after(text, lead)),
            GoldFact(kind=K.INCOME_NOT_CONFIRMED, income_source=S.COMMISSION),
        )
    lead = (
        r"(?:temporary monthly pay is|Gaji bulanan sementara Anda adalah|next salary is reduced to|"
        r"regular salary for the next payroll is|Gaji rutin Anda untuk penggajian berikutnya adalah)"
    )
    if _has(text, lead):
        add("next_payroll_amount", GoldFact(kind=K.SALARY_NEXT_PAYMENT_AMOUNT, income_source=S.SALARY, amount=_amount_after(text, lead)))
    lead = r"(?:one-time arrears adjustment of|penyesuaian tunggakan satu kali sebesar)"
    if _has(text, lead):
        add("arrears_one_time", GoldFact(kind=K.ONE_TIME_INCOME_CONFIRMED, income_source=S.ARREARS, amount=_amount_after(text, lead)))
    if _has(text, r"(?:confirmed salary is now expected on|Gaji yang sudah dikonfirmasi kini diperkirakan masuk pada)"):
        add("salary_date_moved", GoldFact(kind=K.SALARY_DATE_CHANGE, income_source=S.SALARY, effective_date=_first_date(text)))
    lead = (
        r"(?:first salary will be|first salary of|first salary from the new employer is|Gaji pertama Anda sebesar|"
        r"Gaji pertama dari perusahaan baru adalah|Your salary of|Gaji sebesar|Regular salary of)"
    )
    if _has(text, lead):
        add("salary_confirmed_on_date", GoldFact(kind=K.SALARY_CONFIRMED, income_source=S.SALARY, amount=_amount_after(text, lead), effective_date=_first_date(text)))
    if _has(text, r"recurring childcare payment begins"):
        add("new_childcare_expense", GoldFact(kind=K.NEW_RECURRING_EXPENSE, category="childcare"))
    if _has(text, r"(?:Your employment has ended|Hubungan kerja Anda telah berakhir|seasonal contract has ended|Kontrak musiman saat ini telah berakhir)"):
        add("income_ended", GoldFact(kind=K.INCOME_ENDED, income_source=S.SALARY))
    lead = r"(?:remaining confirmed monthly salary is|Sisa gaji bulanan yang dikonfirmasi adalah)"
    if _has(text, lead):
        add("remaining_household_salary", GoldFact(kind=K.INCOME_REMAINING_TOTAL, income_source=S.SALARY, amount=_amount_after(text, lead)))
    if _has(text, r"(?:quarterly bonus|Bonus kuartalan)"):
        add("bonus_pending", GoldFact(kind=K.INCOME_NOT_CONFIRMED, income_source=S.BONUS))
    if _has(text, r"(?:payout is still pending|Pembayaran berikutnya dari \w+ masih tertunda)"):
        add("platform_payout_pending", GoldFact(kind=K.INCOME_NOT_CONFIRMED, income_source=S.PLATFORM_PAYOUT))
    lead = r"(?:client approved an invoice payment of|Klien menyetujui pembayaran faktur sebesar)"
    if _has(text, lead):
        add(
            "invoice_approved_others_pending",
            GoldFact(kind=K.ONE_TIME_INCOME_CONFIRMED, income_source=S.FREELANCE_INVOICE, amount=_amount_after(text, lead), effective_date=_first_date(text)),
            GoldFact(kind=K.INCOME_NOT_CONFIRMED, income_source=S.FREELANCE_INVOICE),
        )
    if _has(text, r"(?:prize claim has been verified|Klaim hadiah Anda sudah diverifikasi)"):
        add("prize_processing", GoldFact(kind=K.INCOME_NOT_CONFIRMED, income_source=S.PRIZE))
    if _has(
        text,
        r"(?:prize proceeds have reached|proceeds from your investment sale have settled|Hasil penjualan investasi Anda sudah masuk|"
        r"reimbursement for your earlier work expense|penggantian atas biaya kerja)",
    ):
        add("credit_already_settled", GoldFact(kind=K.CREDIT_ALREADY_SETTLED))
    if _has(text, r"(?:refund has been initiated|Pengembalian dana sudah diproses|foreign-currency refund is still processing)"):
        add("refund_pending", GoldFact(kind=K.REFUND_NOT_SETTLED, income_source=S.REFUND))
    if _has(text, r"(?:transfer between your two accounts|transfer antara dua rekening)"):
        add("internal_transfer", GoldFact(kind=K.INTERNAL_TRANSFER))
    if _has(text, r"(?:displayed market value|displayed value of the investment|Nilai investasi yang ditampilkan)"):
        add("investment_value", GoldFact(kind=K.INVESTMENT_VALUE_NON_CASH, income_source=S.INVESTMENT))
    match = re.search(r"(?:increases monthly rent by|menaikkan biaya sewa bulanan sebesar)\s*(?P<pct>\d+(?:\.\d+)?)%", text, re.IGNORECASE)
    if match:
        add("rent_increase", GoldFact(kind=K.EXPENSE_PERCENT_CHANGE, percent=Decimal(match.group("pct")), category="rent"))
    if _has(
        text,
        r"(?:previous debit attempt failed|extra card charge is still being investigated|Tagihan kartu tambahan|"
        r"minimum payments due on two separate card|bill was charged in a foreign currency|Tagihan dikenakan dalam mata uang asing)",
    ):
        add("bill_outstanding", GoldFact(kind=K.BILL_STILL_OUTSTANDING))
    if _has(text, r"(?:selected for a cash prize|terpilih untuk menerima hadiah)"):
        add("scam_release_fee", GoldFact(kind=K.SUSPICIOUS_REQUEST))
    if _has(text, r"receipt (?:has|contains) the final"):
        add("receipt_only", GoldFact(kind=K.NO_FINANCIAL_EFFECT))
        if _has(text, r"employer has confirmed"):
            note = "salary claim from a non-employer source must not be accepted"
    if _has(text, r"Gaji rutin untuk penggajian berikutnya sudah dikonfirmasi"):
        add("payroll_confirmed_without_amount", GoldFact(kind=K.NO_FINANCIAL_EFFECT))

    return MessageGold(
        message_id=message.message_id,
        source_type=message.source_type.value,
        families=families or ["unmatched"],
        expected=facts,
        note=note,
    )


def build_message_gold(messages: Iterable[Message]) -> list[MessageGold]:
    return [label_message(message) for message in messages]


def save_models(models: Sequence[BaseModel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([model.model_dump(mode="json") for model in models], indent=1) + "\n", encoding="utf-8")


def load_message_gold(path: Path) -> list[MessageGold]:
    return TypeAdapter(list[MessageGold]).validate_json(path.read_text(encoding="utf-8"))


def load_image_gold(path: Path) -> list[ImageGold]:
    return TypeAdapter(list[ImageGold]).validate_json(path.read_text(encoding="utf-8"))


def load_reviews(path: Path) -> dict[str, EvidenceReview]:
    reviews = [EvidenceReview.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {review.source_id: review for review in reviews}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def score_evidence(
    message_gold: Sequence[MessageGold], image_gold: Sequence[ImageGold], reviews: Mapping[str, EvidenceReview]
) -> EvidenceGoldReport:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    amount_checks = amount_hits = date_checks = date_hits = 0
    disagreements: list[Disagreement] = []

    for gold in message_gold:
        review = reviews.get(gold.message_id)
        accepted: list[AcceptedFact] = review.accepted if review else []
        expected_by_kind: dict[EvidenceKind, list[GoldFact]] = defaultdict(list)
        actual_by_kind: dict[EvidenceKind, list[AcceptedFact]] = defaultdict(list)
        for fact in gold.expected:
            expected_by_kind[fact.kind].append(fact)
        for fact in accepted:
            actual_by_kind[fact.kind].append(fact)
        for kind in set(expected_by_kind) | set(actual_by_kind):
            wanted = sorted(expected_by_kind[kind], key=lambda fact: (fact.amount or Decimal(0), str(fact.income_source)))
            got = sorted(actual_by_kind[kind], key=lambda fact: (fact.original_amount or Decimal(0), str(fact.income_source)))
            matched = min(len(wanted), len(got))
            tally = counts[kind.value]
            tally[0] += matched
            tally[1] += len(got) - matched
            tally[2] += len(wanted) - matched
            if len(wanted) != len(got):
                disagreements.append(
                    Disagreement(
                        source_id=gold.message_id,
                        expected=f"{len(wanted)} x {kind.value}",
                        actual=f"{len(got)} x {kind.value}",
                        note=review.summary if review else "no extraction",
                    )
                )
            for want, have in zip(wanted, got):
                if want.amount is not None:
                    amount_checks += 1
                    if have.original_amount == want.amount:
                        amount_hits += 1
                    else:
                        disagreements.append(Disagreement(source_id=gold.message_id, expected=f"{kind.value} amount {want.amount}", actual=str(have.original_amount)))
                if want.effective_date is not None:
                    date_checks += 1
                    if have.effective_date == want.effective_date:
                        date_hits += 1
                    else:
                        disagreements.append(Disagreement(source_id=gold.message_id, expected=f"{kind.value} date {want.effective_date}", actual=str(have.effective_date)))

    strict = lenient = 0
    for gold in image_gold:
        review = reviews.get(gold.image_id)
        amounts = [fact.original_amount for fact in (review.accepted if review else []) if fact.kind is K.DOCUMENT_AMOUNT]
        actual = amounts[0] if amounts else None
        if actual == gold.expected_amount:
            strict += 1
            lenient += 1
        else:
            if actual is not None and actual in gold.alternatives:
                lenient += 1
            disagreements.append(
                Disagreement(source_id=gold.image_id, expected=f"{gold.expected_amount} ({gold.label})", actual=str(actual), note=gold.note)
            )

    kind_scores = [
        KindScore(
            kind=kind,
            true_positive=tp,
            false_positive=fp,
            false_negative=fn,
            precision=_ratio(tp, tp + fp),
            recall=_ratio(tp, tp + fn),
        )
        for kind, (tp, fp, fn) in sorted(counts.items())
    ]
    forecast = [score for score in kind_scores if EvidenceKind(score.kind) in FORECAST_KINDS]
    tp = sum(score.true_positive for score in forecast)
    return EvidenceGoldReport(
        messages=len(message_gold),
        images=len(image_gold),
        unmatched_messages=[gold.message_id for gold in message_gold if gold.families == ["unmatched"]],
        kind_scores=kind_scores,
        forecast_precision=_ratio(tp, tp + sum(score.false_positive for score in forecast)),
        forecast_recall=_ratio(tp, tp + sum(score.false_negative for score in forecast)),
        amount_match_rate=_ratio(amount_hits, amount_checks),
        date_match_rate=_ratio(date_hits, date_checks),
        image_accuracy_strict=_ratio(strict, len(image_gold)),
        image_accuracy_lenient=_ratio(lenient, len(image_gold)),
        disagreements=disagreements,
    )


def render_evidence_report(report: EvidenceGoldReport) -> str:
    lines = [
        "# S4 evidence gold",
        "",
        f"- Messages labelled: {report.messages} (unmatched: {len(report.unmatched_messages)}); images labelled: {report.images}",
        f"- Forecast-affecting facts: precision {report.forecast_precision:.3f}, recall {report.forecast_recall:.3f}",
        f"- Amount exact match {report.amount_match_rate:.3f}; date exact match {report.date_match_rate:.3f}",
        f"- Image amount accuracy: strict {report.image_accuracy_strict:.3f}, lenient {report.image_accuracy_lenient:.3f}",
        "",
        "| Kind | TP | FP | FN | Precision | Recall |",
        "|---|---|---|---|---|---|",
        *[f"| {s.kind} | {s.true_positive} | {s.false_positive} | {s.false_negative} | {s.precision:.2f} | {s.recall:.2f} |" for s in report.kind_scores],
        "",
        "## Disagreements",
        "",
        *[f"- {d.source_id}: expected {d.expected}; actual {d.actual}" + (f" ({d.note})" if d.note else "") for d in report.disagreements],
    ]
    return "\n".join(lines) + "\n"
