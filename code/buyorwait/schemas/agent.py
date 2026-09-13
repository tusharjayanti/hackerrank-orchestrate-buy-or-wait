"""Agent tool schemas (strict tool inputs, so no defaults or numeric constraints) and the validated outcome."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .evidence import ConfidenceLabel
from .obs import GuardrailViolation


class EvaluateScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str


class KeyFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    source_ids: list[str]


class SubmitDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    ambiguity_resolution: str
    decision_explanation: str
    key_facts: list[KeyFact]
    confidence: ConfidenceLabel
    confidence_score: float


class AgentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    accepted: bool
    submission: SubmitDecisionInput | None = None
    turns: int = 0
    repairs: int = 0
    violations: list[GuardrailViolation] = Field(default_factory=list)
    error: str | None = None
    usage: list[dict[str, Any]] = Field(default_factory=list)
    cached_replay: bool = False
