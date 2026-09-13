"""Agent mode: engine scenarios -> context pack -> agent (concurrent) -> validated final rows with engine fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..evidence.extract import EvidenceStore
from ..guardrails.contract import check_output_row
from ..obs.logging import get_logger
from ..obs.tracing import submit_in_context
from ..output.writer import OutputRow
from ..pipeline import EnginePipeline, RequestResult
from ..schemas.agent import AgentOutcome
from ..schemas.domain import PurchaseRequest
from .context import AgentContext, build_context
from .loop import DecisionAgent
from .scenarios import BASE_SCENARIO, detect_scenarios

logger = get_logger("agent.runner")


@dataclass
class PreparedRequest:
    request: PurchaseRequest
    results: dict[str, RequestResult]
    context: AgentContext


@dataclass
class AgentResult:
    request: PurchaseRequest
    row: OutputRow
    base: RequestResult
    outcome: AgentOutcome | None
    scenario_id: str
    fell_back: bool


class AgentRunner:
    def __init__(self, pipeline: EnginePipeline, agent: DecisionAgent, evidence: EvidenceStore | None, concurrency: int) -> None:
        self.pipeline = pipeline
        self.agent = agent
        self.evidence = evidence
        self.concurrency = concurrency

    def prepare(self, request: PurchaseRequest) -> PreparedRequest:
        dataset = self.pipeline.dataset
        entries = self.pipeline.ledger.get(request.user_id, [])
        scenarios = detect_scenarios(entries, request, self.pipeline.knobs)
        results = {scenario.scenario_id: self.pipeline.run_request(request, scenario.knobs) for scenario in scenarios}
        facts = self.evidence.facts_for_user(request.user_id) if self.evidence else []
        reviews = [review for review in self.evidence.reviews.values() if review.user_id == request.user_id] if self.evidence else []
        context = build_context(
            scenarios,
            results,
            dataset.profiles[request.user_id],
            dataset.options_by_request.get(request.request_id, []),
            facts,
            reviews,
        )
        return PreparedRequest(request, results, context)

    def finalize(self, prepared: PreparedRequest, outcome: AgentOutcome | None) -> AgentResult:
        base = prepared.results[BASE_SCENARIO]
        if outcome is None or not outcome.accepted or outcome.submission is None:
            return AgentResult(prepared.request, base.row, base, outcome, BASE_SCENARIO, fell_back=True)
        chosen = prepared.results[outcome.submission.scenario_id]
        row = chosen.row.model_copy(update={"decision_explanation": outcome.submission.decision_explanation.strip()})
        dataset = self.pipeline.dataset
        request = prepared.request
        violations = check_output_row(
            row, request, dataset.profiles[request.user_id], dataset.options_by_request.get(request.request_id, []), dataset.events_by_id
        )
        if violations or chosen.fell_back:
            logger.warning("agent row rejected by G4; using engine row", extra={"fields": {"request_id": request.request_id}})
            return AgentResult(request, base.row, base, outcome, BASE_SCENARIO, fell_back=True)
        return AgentResult(request, row, base, outcome, outcome.submission.scenario_id, fell_back=False)

    def run(self, requests: list[PurchaseRequest]) -> list[AgentResult]:
        prepared = [self.prepare(request) for request in requests]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [submit_in_context(pool, self.agent.decide, item.context) for item in prepared]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # noqa: BLE001 - any agent failure falls back to the engine row
                    logger.error("agent crashed", extra={"fields": {"error": repr(exc)}})
                    outcomes.append(None)
        return [self.finalize(item, outcome) for item, outcome in zip(prepared, outcomes)]
