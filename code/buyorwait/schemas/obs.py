"""Observability and guardrail records written as JSONL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import Severity, SpanStatus


class GuardrailViolation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: str  # G1..G9, see DESIGN.md section 6
    code: str
    severity: Severity
    message: str
    entity_id: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SpanRecord(BaseModel):
    """A finished span, shaped like an OpenTelemetry span so an OTLP exporter can be added later."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    status: SpanStatus
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class LLMCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    run_id: str
    trace_id: str
    span_id: str
    request_id: str | None
    purpose: str
    provider: str = "anthropic"
    model: str
    response_model: str | None = None
    response_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    stop_reason: str | None = None
    latency_ms: float = 0.0
    cost_usd: float | None = None
    prompt_version: str
    prompt_sha256: str
    cached_replay: bool = False
    batch: bool = False
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    run_id: str
    trace_id: str
    span_id: str
    request_id: str | None
    tool_name: str
    arguments: dict[str, Any]
    result_summary: str
    duration_ms: float
    is_error: bool = False
