"""Pipeline: evidence-adjusted engine decision per request, a validated output row, and a safe fallback when G4 fails."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from .engine.decide import EngineDecision, decide
from .engine.knobs import EngineKnobs
from .evidence.extract import EvidenceStore
from .evidence.resolve import resolve_adjustments
from .guardrails.contract import check_output_row
from .ingest.lifecycle import build_ledger
from .ingest.loaders import Dataset
from .obs.logging import get_logger
from .obs.tracing import Tracer
from .output.explain import explain
from .output.writer import OutputRow
from .schemas.domain import LedgerEntry, PurchaseRequest
from .schemas.enums import AffordabilityStatus, PaymentMethod
from .schemas.obs import GuardrailViolation

logger = get_logger("pipeline")


@dataclass
class RequestResult:
    request: PurchaseRequest
    decision: EngineDecision
    row: OutputRow
    violations: list[GuardrailViolation] = field(default_factory=list)
    fell_back: bool = False


def patch_blank_amounts(
    ledger: dict[str, list[LedgerEntry]], overrides: Mapping[str, Decimal]
) -> dict[str, list[LedgerEntry]]:
    """Fill blank event amounts with validated image amounts (home currency)."""
    if not overrides:
        return ledger
    return {
        user_id: [
            entry.model_copy(update={"amount_home": overrides[entry.event_id], "notes": (*entry.notes, "amount from linked image")})
            if entry.amount_home is None and entry.event_id in overrides
            else entry
            for entry in entries
        ]
        for user_id, entries in ledger.items()
    }


class EnginePipeline:
    def __init__(
        self,
        dataset: Dataset,
        knobs: EngineKnobs | None = None,
        tracer: Tracer | None = None,
        ledger: dict[str, list[LedgerEntry]] | None = None,
        evidence: EvidenceStore | None = None,
    ) -> None:
        self.dataset = dataset
        self.knobs = knobs or EngineKnobs()
        self.tracer = tracer
        self.evidence = evidence
        ledger = ledger if ledger is not None else build_ledger(dataset.events, dataset.profiles, dataset.fx)
        self.ledger = patch_blank_amounts(ledger, evidence.amount_overrides()) if evidence else ledger

    def run_request(self, request: PurchaseRequest, knobs: EngineKnobs | None = None) -> RequestResult:
        if self.tracer is None:
            return self._run(request, knobs)
        with self.tracer.span("request.decide", request_id=request.request_id) as span:
            result = self._run(request, knobs)
            span.set_attributes(
                **{
                    "decision.status": result.row.affordability_status.value,
                    "decision.method": result.row.recommended_payment_method.value,
                    "evidence.applied_facts": len(result.decision.evidence.applied_fact_ids) if result.decision.evidence else 0,
                    "guardrails.violations": len(result.violations),
                    "decision.fell_back": result.fell_back,
                }
            )
            return result

    def _run(self, request: PurchaseRequest, knobs: EngineKnobs | None = None) -> RequestResult:
        dataset = self.dataset
        profile = dataset.profiles[request.user_id]
        options = dataset.options_by_request.get(request.request_id, [])
        entries = self.ledger.get(request.user_id, [])
        knobs = knobs or self.knobs
        adjustments = (
            resolve_adjustments(self.evidence.facts_for_user(request.user_id), request, entries, knobs)
            if self.evidence
            else None
        )
        decision = decide(request, profile, options, entries, knobs, adjustments)
        row = OutputRow.from_decision(decision, explain(decision))
        violations = check_output_row(row, request, profile, options, dataset.events_by_id)
        if not violations:
            return RequestResult(request, decision, row)

        logger.warning(
            "G4 violations; falling back to not_recommended",
            extra={"fields": {"request_id": request.request_id, "codes": [violation.code for violation in violations]}},
        )
        fallback = row.model_copy(
            update={
                "affordability_status": AffordabilityStatus.NOT_AFFORDABLE,
                "recommended_payment_method": PaymentMethod.NOT_RECOMMENDED,
                "payment_plan": "none",
                "spending_changes_needed": "none",
                "decision_explanation": (
                    "Do not proceed yet. The recommended plan failed validation, so no payment is recommended "
                    "until it can be confirmed safe."
                ),
            }
        )
        return RequestResult(request, decision, fallback, violations, fell_back=True)
