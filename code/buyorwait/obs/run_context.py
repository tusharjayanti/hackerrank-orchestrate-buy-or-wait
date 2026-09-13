"""Per-run output directory and the JSONL sinks shared by every pipeline stage."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.obs import GuardrailViolation
from .sinks import JsonlSink
from .tracing import Tracer


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    tracer: Tracer
    llm_calls: JsonlSink
    tool_calls: JsonlSink
    guardrails: JsonlSink
    evidence: JsonlSink

    @classmethod
    def create(cls, runs_dir: Path, run_id: str | None = None) -> RunContext:
        run_id = run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            tracer=Tracer([JsonlSink(run_dir / "spans.jsonl")]),
            llm_calls=JsonlSink(run_dir / "llm_calls.jsonl"),
            tool_calls=JsonlSink(run_dir / "tool_calls.jsonl"),
            guardrails=JsonlSink(run_dir / "guardrails.jsonl"),
            evidence=JsonlSink(run_dir / "evidence.jsonl"),
        )

    def record_violations(self, violations: Iterable[GuardrailViolation]) -> None:
        for violation in violations:
            self.guardrails.write(violation)
