from types import SimpleNamespace

import pytest

from buyorwait.agent.context import AgentContext, build_context
from buyorwait.agent.loop import DecisionAgent
from buyorwait.agent.scenarios import detect_scenarios
from buyorwait.config import Settings
from buyorwait.guardrails.grounding import check_submission
from buyorwait.obs.llm_client import LLMClient
from buyorwait.obs.run_context import RunContext
from buyorwait.pipeline import EnginePipeline
from buyorwait.schemas.agent import KeyFact, SubmitDecisionInput
from buyorwait.schemas.evidence import ConfidenceLabel

GOOD = "Pay EUR 166.61 today. This leaves at least EUR 600 available over the next 90 days."


@pytest.fixture(scope="module")
def context(dataset, ledger) -> AgentContext:
    pipeline = EnginePipeline(dataset, ledger=ledger)
    request = next(r for r in dataset.sample_requests if r.request_id == "request_09")
    scenarios = detect_scenarios(pipeline.ledger[request.user_id], request, pipeline.knobs)
    results = {scenario.scenario_id: pipeline.run_request(request, scenario.knobs) for scenario in scenarios}
    return build_context(
        scenarios, results, dataset.profiles[request.user_id], dataset.options_by_request[request.request_id], [], []
    )


def submission(text: str, sources=("request_09",), scenario: str = "base", score: float = 0.9) -> SubmitDecisionInput:
    return SubmitDecisionInput(
        scenario_id=scenario,
        ambiguity_resolution="none",
        decision_explanation=text,
        key_facts=[KeyFact(statement="fact", source_ids=list(sources))],
        confidence=ConfidenceLabel.HIGH,
        confidence_score=score,
    )


def codes(violations) -> set[str]:
    return {violation.code for violation in violations}


def test_grounded_submission_passes(context):
    assert check_submission(submission(GOOD), context) == []


def test_ungrounded_number_is_rejected(context):
    bad = submission("Pay EUR 166.61 today. This leaves at least EUR 700 available.")
    assert "ungrounded_number" in codes(check_submission(bad, context))


def test_explanation_must_match_the_chosen_plan(context):
    assert "explanation_inconsistent" in codes(check_submission(submission("Wait for your next salary."), context))


def test_unknown_citations_scenarios_and_confidence_are_rejected(context):
    assert "unknown_citation" in codes(check_submission(submission(GOOD, sources=("event_999999",)), context))
    assert "unknown_scenario" in codes(check_submission(submission(GOOD, scenario="made_up"), context))
    assert "confidence_label_mismatch" in codes(check_submission(submission(GOOD, score=0.2), context))


class ScriptedMessages:
    def __init__(self, inputs) -> None:
        self.inputs = list(inputs)
        self.calls = 0

    def create(self, **kwargs):
        payload = self.inputs[min(self.calls, len(self.inputs) - 1)]
        self.calls += 1
        block = SimpleNamespace(type="tool_use", id=f"toolu_{self.calls}", name="submit_decision", input=payload)
        return SimpleNamespace(
            content=[block],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0),
            model="claude-sonnet-5",
            id=f"msg_{self.calls}",
        )


def make_agent(tmp_path, scripted: ScriptedMessages) -> DecisionAgent:
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", runs_dir=tmp_path / "runs", max_agent_turns=5)
    run = RunContext.create(settings.runs_dir, "t")
    return DecisionAgent(LLMClient(settings, run, client=SimpleNamespace(messages=scripted)), settings, run)


def test_agent_repairs_an_ungrounded_submission_and_caches_the_outcome(context, tmp_path):
    bad = submission("Pay EUR 166.61 today. This leaves at least EUR 700 available.").model_dump(mode="json")
    scripted = ScriptedMessages([bad, submission(GOOD).model_dump(mode="json")])
    agent = make_agent(tmp_path, scripted)

    outcome = agent.decide(context)
    assert outcome.accepted and outcome.repairs == 1 and scripted.calls == 2

    replay = agent.decide(context)
    assert replay.cached_replay and replay.submission == outcome.submission
    assert scripted.calls == 2


def test_agent_gives_up_after_repeated_invalid_submissions(context, tmp_path):
    bad = submission("Wait for your next salary.").model_dump(mode="json")
    scripted = ScriptedMessages([bad])
    outcome = make_agent(tmp_path, scripted).decide(context)
    assert not outcome.accepted
    assert outcome.error
    assert scripted.calls == 3
