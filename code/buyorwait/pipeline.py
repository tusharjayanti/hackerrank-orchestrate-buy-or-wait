"""Engine-mode pipeline: one validated output row per request, with a safe fallback when G4 fails."""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine.decide import EngineDecision, decide
from .engine.knobs import EngineKnobs
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


class EnginePipeline:
    def __init__(
        self,
        dataset: Dataset,
        knobs: EngineKnobs | None = None,
        tracer: Tracer | None = None,
        ledger: dict[str, list[LedgerEntry]] | None = None,
    ) -> None:
        self.dataset = dataset
        self.knobs = knobs or EngineKnobs()
        self.tracer = tracer
        self.ledger = ledger if ledger is not None else build_ledger(dataset.events, dataset.profiles, dataset.fx)

    def run_request(self, request: PurchaseRequest) -> RequestResult:
        if self.tracer is None:
            return self._run(request)
        with self.tracer.span("request.decide", request_id=request.request_id) as span:
            result = self._run(request)
            span.set_attributes(
                **{
                    "decision.status": result.row.affordability_status.value,
                    "decision.method": result.row.recommended_payment_method.value,
                    "guardrails.violations": len(result.violations),
                    "decision.fell_back": result.fell_back,
                }
            )
            return result

    def _run(self, request: PurchaseRequest) -> RequestResult:
        dataset = self.dataset
        profile = dataset.profiles[request.user_id]
        options = dataset.options_by_request.get(request.request_id, [])
        decision = decide(request, profile, options, self.ledger.get(request.user_id, []), self.knobs)
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
                    f"Do not proceed yet. The recommended plan failed validation, so no payment is recommended "
                    f"until it can be confirmed safe."
                ),
            }
        )
        return RequestResult(request, decision, fallback, violations, fell_back=True)
