"""Manual tool-use loop for the decision agent, with G5 validation, bounded repairs and outcome caching."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..guardrails.grounding import check_submission
from ..obs.llm_client import LLMClient, LLMError, canonical_json, usage_dict
from ..obs.logging import get_logger
from ..obs.run_context import RunContext
from ..obs.tracing import current_span
from ..schemas.agent import AgentOutcome, EvaluateScenarioInput, SubmitDecisionInput
from ..schemas.obs import ToolCallRecord
from .context import AgentContext
from .prompts import AGENT_PROMPT_VERSION, AGENT_SYSTEM, TOOLS

logger = get_logger("agent")
MAX_REPAIRS = 2


class DecisionAgent:
    def __init__(self, llm: LLMClient, settings: Settings, run: RunContext) -> None:
        self.llm = llm
        self.settings = settings
        self.run = run
        self._cache_dir = settings.cache_dir / "agent"

    def _cache_key(self, context: AgentContext) -> str:
        material = {"context": context.prompt_json(), "system": AGENT_SYSTEM, "tools": TOOLS, "version": AGENT_PROMPT_VERSION, "model": self.llm.model}
        return hashlib.sha256(canonical_json(material).encode()).hexdigest()

    def decide(self, context: AgentContext, use_cache: bool = True) -> AgentOutcome:
        key = self._cache_key(context)
        cache_path = self._cache_dir / f"{key}.json"
        with self.run.tracer.span("agent.request", request_id=context.request_id) as span:
            if use_cache and cache_path.exists():
                outcome = AgentOutcome.model_validate_json(cache_path.read_text(encoding="utf-8"))
                for usage in outcome.usage:
                    self.llm.record_replay(purpose="agent.turn", prompt_version=AGENT_PROMPT_VERSION, prompt_sha=key, usage=usage, request_id=context.request_id)
                outcome = outcome.model_copy(update={"cached_replay": True})
            else:
                outcome = self._loop(context, key)
                if outcome.accepted:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(outcome.model_dump_json(), encoding="utf-8")
            span.set_attributes(
                **{"agent.accepted": outcome.accepted, "agent.turns": outcome.turns, "agent.repairs": outcome.repairs, "agent.cached_replay": outcome.cached_replay}
            )
            self.run.record_violations(outcome.violations)
            return outcome

    def _loop(self, context: AgentContext, key: str) -> AgentOutcome:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"<decision_context>\n{context.prompt_json()}\n</decision_context>\n\n"
                "Choose the scenario, write the explanation, and call submit_decision.",
            }
        ]
        outcome = AgentOutcome(request_id=context.request_id, accepted=False)
        for turn in range(1, self.settings.max_agent_turns + 1):
            outcome.turns = turn
            try:
                response = self.llm.create(
                    purpose="agent.turn",
                    prompt_version=AGENT_PROMPT_VERSION,
                    request_id=context.request_id,
                    max_tokens=6000,
                    system=AGENT_SYSTEM,
                    tools=TOOLS,
                    messages=messages,
                    output_config={"effort": self.settings.agent_effort},
                    cache_control={"type": "ephemeral"},
                )
            except LLMError as exc:
                outcome.error = str(exc)
                return outcome
            outcome.usage.append(usage_dict(response.usage))
            if response.stop_reason == "refusal":
                outcome.error = "model refused"
                return outcome
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if not tool_uses:
                messages.append({"role": "user", "content": "Call submit_decision to finish."})
                continue

            results = []
            for block in tool_uses:
                started = time.perf_counter()
                content, is_error = self._handle_tool(block, context, outcome)
                self._record_tool(context.request_id, block, content, is_error, started)
                if outcome.accepted:
                    return outcome
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
            if outcome.repairs > MAX_REPAIRS:
                outcome.error = "submission still invalid after repairs"
                return outcome
            messages.append({"role": "user", "content": results})
        outcome.error = outcome.error or "turn limit reached"
        return outcome

    def _handle_tool(self, block: Any, context: AgentContext, outcome: AgentOutcome) -> tuple[str, bool]:
        if block.name == "evaluate_scenario":
            try:
                scenario_id = EvaluateScenarioInput.model_validate(block.input).scenario_id
            except ValidationError as exc:
                return f"invalid input: {exc.errors(include_url=False)}", True
            summary = context.scenario_summaries.get(scenario_id)
            if summary is None:
                return f"unknown scenario_id {scenario_id!r}; choose from {sorted(context.scenario_summaries)}", True
            return json.dumps(summary, sort_keys=True), False
        if block.name == "submit_decision":
            try:
                submission = SubmitDecisionInput.model_validate(block.input)
            except ValidationError as exc:
                outcome.repairs += 1
                return f"invalid submission: {exc.errors(include_url=False)}", True
            violations = check_submission(submission, context)
            if not violations:
                outcome.accepted = True
                outcome.submission = submission
                return "accepted", False
            outcome.repairs += 1
            outcome.violations.extend(violations)
            return json.dumps({"errors": [violation.message for violation in violations]}), True
        return f"unknown tool {block.name!r}", True

    def _record_tool(self, request_id: str, block: Any, content: str, is_error: bool, started: float) -> None:
        span = current_span()
        self.run.tool_calls.write(
            ToolCallRecord(
                timestamp=datetime.now(UTC),
                run_id=self.run.run_id,
                trace_id=span.trace_id if span else "",
                span_id=span.span_id if span else "",
                request_id=request_id,
                tool_name=block.name,
                arguments=dict(block.input) if isinstance(block.input, dict) else {"raw": str(block.input)},
                result_summary=content[:300],
                duration_ms=(time.perf_counter() - started) * 1000,
                is_error=is_error,
            )
        )
        if is_error:
            logger.warning("agent tool error", extra={"fields": {"request_id": request_id, "tool": block.name, "result": content[:300]}})
