"""Guardrail G5: an agent submission may only restate grounded numbers, cite known sources and match the chosen plan."""

from __future__ import annotations

from typing import Any

from ..evidence.validate import LABEL_RANGES, numbers_in
from ..schemas.agent import SubmitDecisionInput
from ..schemas.enums import Severity
from ..schemas.obs import GuardrailViolation
from .injection import scan_for_injection

MAX_EXPLANATION_CHARS = 420


def check_submission(submission: SubmitDecisionInput, context: Any) -> list[GuardrailViolation]:
    """`context` is an agent.context.AgentContext (typed loosely to keep guardrails free of agent imports)."""
    violations: list[GuardrailViolation] = []

    def fail(code: str, message: str, **details: Any) -> None:
        violations.append(
            GuardrailViolation(
                layer="G5", code=code, severity=Severity.ERROR, message=message, request_id=context.request_id, details=details
            )
        )

    scenario = submission.scenario_id
    if scenario not in context.scenario_summaries:
        fail("unknown_scenario", f"scenario_id {scenario!r} is not one of {sorted(context.scenario_summaries)}")
        return violations

    explanation = submission.decision_explanation.strip()
    if not explanation:
        fail("empty_explanation", "decision_explanation is empty")
    if len(explanation) > MAX_EXPLANATION_CHARS:
        fail("explanation_too_long", f"decision_explanation has {len(explanation)} characters; keep it under {MAX_EXPLANATION_CHARS}")
    ungrounded = sorted(str(number) for number in numbers_in(explanation) - context.allowed_numbers[scenario])
    if ungrounded:
        fail("ungrounded_number", f"numbers not present in the context: {ungrounded}; copy amounts and dates from the display strings")
    lowered = explanation.lower()
    for alternatives in context.required_phrases.get(scenario, []):
        if not any(phrase.lower() in lowered for phrase in alternatives):
            fail("explanation_inconsistent", f"explanation must state the chosen plan and include one of {list(alternatives)}")
    if scan_for_injection(explanation):
        fail("instruction_text_in_output", "explanation contains instruction-like text")

    if not submission.key_facts:
        fail("missing_key_facts", "provide at least one key fact with source ids")
    unknown = sorted({source for fact in submission.key_facts for source in fact.source_ids} - context.allowed_source_ids)
    if unknown:
        fail("unknown_citation", f"source ids not in the context: {unknown}")

    low, high = LABEL_RANGES[submission.confidence]
    if not 0.0 <= submission.confidence_score <= 1.0 or not low - 0.05 <= submission.confidence_score <= high + 0.05:
        fail("confidence_label_mismatch", f"confidence_score {submission.confidence_score} does not match {submission.confidence}")
    return violations
