"""The only module that calls the Anthropic API: tracing, token accounting, result caching and batching."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..schemas.obs import LLMCallRecord
from .logging import get_logger
from .pricing import estimate_cost_usd
from .run_context import RunContext
from .tracing import SpanHandle

try:  # the SDK's structured-output helper uses this to make Pydantic schemas API-compatible
    from anthropic.lib._parse._transform import transform_schema
except ImportError:  # pragma: no cover - older SDKs

    def transform_schema(schema: dict[str, Any]) -> dict[str, Any]:
        return schema


OutputModel = TypeVar("OutputModel", bound=BaseModel)
logger = get_logger("llm")

_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
_UNUSABLE_STOP_REASONS = {"refusal", "max_tokens"}


class LLMError(RuntimeError):
    """An LLM call failed or returned nothing usable; callers fall back to deterministic behaviour."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def usage_dict(usage: Any) -> dict[str, int]:
    return {name: int(getattr(usage, name, 0) or 0) for name in _USAGE_FIELDS}


@dataclass(frozen=True)
class StructuredJob:
    """One structured-output request, sent on its own through parse() or together with others through parse_batch()."""

    custom_id: str
    purpose: str
    output_model: type[BaseModel]
    system: str
    messages: list[dict[str, Any]]
    prompt_version: str
    effort: str
    max_tokens: int
    request_id: str | None = None


class LLMClient:
    def __init__(self, settings: Settings, run: RunContext, client: Any | None = None) -> None:
        if client is None:
            if settings.anthropic_api_key is None:
                raise LLMError("ANTHROPIC_API_KEY is not set; add it to .env or the environment")
            client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                max_retries=settings.llm_max_retries,
                timeout=settings.llm_timeout_seconds,
            )
        self._client = client
        self._run = run
        self.model = settings.model
        self._cache_dir = settings.cache_dir / "llm"

    def structured_request(
        self,
        *,
        output_model: type[BaseModel],
        system: str,
        messages: list[dict[str, Any]],
        prompt_version: str,
        effort: str,
        max_tokens: int,
    ) -> tuple[dict[str, Any], str, Path]:
        """Request parameters, prompt hash and cache path, shared by parse() and parse_batch()."""
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": effort},
        }
        prompt_sha = hashlib.sha256(
            canonical_json(
                {"params": params, "schema": output_model.model_json_schema(), "prompt_version": prompt_version}
            ).encode()
        ).hexdigest()
        return params, prompt_sha, self._cache_dir / f"{prompt_sha}.json"

    def parse(
        self,
        *,
        purpose: str,
        output_model: type[OutputModel],
        system: str,
        messages: list[dict[str, Any]],
        prompt_version: str,
        effort: str,
        max_tokens: int = 8000,
        request_id: str | None = None,
        use_cache: bool = True,
        record_replay: bool = True,
    ) -> OutputModel:
        """Structured-output call validated against `output_model`, cached on disk by prompt hash."""
        params, prompt_sha, cache_path = self.structured_request(
            output_model=output_model,
            system=system,
            messages=messages,
            prompt_version=prompt_version,
            effort=effort,
            max_tokens=max_tokens,
        )

        with self._run.tracer.span(f"llm.{purpose}", request_id=request_id, **self._request_attributes(purpose)) as span:
            if use_cache and cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if record_replay:
                    self._record(
                        span,
                        purpose=purpose,
                        prompt_version=prompt_version,
                        prompt_sha=prompt_sha,
                        usage=cached["usage"],
                        response_model=cached.get("response_model"),
                        response_id=cached.get("response_id"),
                        stop_reason=cached.get("stop_reason"),
                        cached_replay=True,
                    )
                return output_model.model_validate(cached["output"])

            started = time.perf_counter()
            try:
                response = self._client.messages.parse(output_format=output_model, **params)
            except anthropic.APIError as exc:
                self._record(span, purpose=purpose, prompt_version=prompt_version, prompt_sha=prompt_sha, error=repr(exc))
                raise LLMError(f"{purpose} call failed: {exc}") from exc
            latency_ms = (time.perf_counter() - started) * 1000

            parsed = getattr(response, "parsed_output", None)
            error = None
            if response.stop_reason in _UNUSABLE_STOP_REASONS:
                error = f"stop_reason={response.stop_reason}"
            elif parsed is None:
                error = "response had no parsed output"
            usage = usage_dict(response.usage)
            self._record(
                span,
                purpose=purpose,
                prompt_version=prompt_version,
                prompt_sha=prompt_sha,
                usage=usage,
                response_model=response.model,
                response_id=response.id,
                stop_reason=response.stop_reason,
                latency_ms=latency_ms,
                error=error,
            )
            if error:
                raise LLMError(f"{purpose}: {error}")
            self._write_cache(cache_path, parsed, usage, response.model, response.id, response.stop_reason)
            return parsed

    def parse_batch(
        self,
        jobs: Sequence[StructuredJob],
        *,
        poll_seconds: float,
        timeout_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, bool]:
        """Submit every uncached job as one Message Batch (50% price) and store valid results in the parse() cache.

        Returns custom_id -> True when a validated result is cached. Anything else (API error, timeout, errored or
        invalid item) is left uncached so the caller's normal parse() call handles it synchronously.
        """
        outcome: dict[str, bool] = {}
        pending: dict[str, tuple[StructuredJob, str, Path]] = {}
        requests: list[dict[str, Any]] = []
        for job in jobs:
            params, prompt_sha, cache_path = self.structured_request(
                output_model=job.output_model,
                system=job.system,
                messages=job.messages,
                prompt_version=job.prompt_version,
                effort=job.effort,
                max_tokens=job.max_tokens,
            )
            if cache_path.exists():
                outcome[job.custom_id] = True
                continue
            output_format = {"type": "json_schema", "schema": transform_schema(job.output_model.model_json_schema())}
            requests.append(
                {
                    "custom_id": job.custom_id,
                    "params": {**params, "output_config": {**params["output_config"], "format": output_format}},
                }
            )
            pending[job.custom_id] = (job, prompt_sha, cache_path)
        if not requests:
            return outcome

        with self._run.tracer.span("llm.batch", **{"batch.requests": len(requests), "gen_ai.request.model": self.model}) as span:
            try:
                batch = self._client.messages.batches.create(requests=requests)
                span.set_attribute("batch.id", batch.id)
                waited = 0.0
                while batch.processing_status != "ended" and waited < timeout_seconds:
                    sleep(poll_seconds)
                    waited += poll_seconds
                    batch = self._client.messages.batches.retrieve(batch.id)
                span.set_attribute("batch.wait_seconds", waited)
                if batch.processing_status != "ended":
                    self._client.messages.batches.cancel(batch.id)
                    logger.error("batch timed out; falling back to synchronous calls", extra={"fields": {"batch_id": batch.id}})
                    return outcome | dict.fromkeys(pending, False)
                results = list(self._client.messages.batches.results(batch.id))
            except anthropic.APIError as exc:
                logger.error("batch failed; falling back to synchronous calls", extra={"fields": {"error": repr(exc)}})
                span.set_attribute("batch.error", repr(exc))
                return outcome | dict.fromkeys(pending, False)

            for entry in results:
                if entry.custom_id not in pending:
                    continue
                job, prompt_sha, cache_path = pending[entry.custom_id]
                result = entry.result
                if result.type != "succeeded":
                    outcome[entry.custom_id] = False
                    continue
                message = result.message
                usage = usage_dict(message.usage)
                text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
                parsed, error = None, None
                if message.stop_reason in _UNUSABLE_STOP_REASONS:
                    error = f"stop_reason={message.stop_reason}"
                else:
                    try:
                        parsed = job.output_model.model_validate_json(text)
                    except ValidationError as exc:
                        error = f"invalid structured output ({exc.error_count()} errors)"
                self.record_batch_call(
                    purpose=job.purpose,
                    prompt_version=job.prompt_version,
                    prompt_sha=prompt_sha,
                    usage=usage,
                    response_model=message.model,
                    response_id=message.id,
                    stop_reason=message.stop_reason,
                    request_id=job.request_id,
                    error=error,
                )
                if parsed is None:
                    outcome[entry.custom_id] = False
                    continue
                self._write_cache(cache_path, parsed, usage, message.model, message.id, message.stop_reason)
                outcome[entry.custom_id] = True
            for custom_id in pending:
                outcome.setdefault(custom_id, False)
            span.set_attributes(
                **{"batch.succeeded": sum(outcome[c] for c in pending), "batch.fallback": sum(not outcome[c] for c in pending)}
            )
        return outcome

    def create(self, *, purpose: str, prompt_version: str, request_id: str | None = None, **params: Any) -> Any:
        """Uncached Messages API call (agent tool-use turns). Prompt caching is not requested: each request's
        context is unique, so cache writes cost a premium without later reads."""
        params = {"model": self.model, **params}
        prompt_sha = hashlib.sha256(canonical_json({"params": params, "prompt_version": prompt_version}).encode()).hexdigest()
        with self._run.tracer.span(f"llm.{purpose}", request_id=request_id, **self._request_attributes(purpose)) as span:
            started = time.perf_counter()
            try:
                response = self._client.messages.create(**params)
            except anthropic.APIError as exc:
                self._record(span, purpose=purpose, prompt_version=prompt_version, prompt_sha=prompt_sha, error=repr(exc))
                raise LLMError(f"{purpose} call failed: {exc}") from exc
            self._record(
                span,
                purpose=purpose,
                prompt_version=prompt_version,
                prompt_sha=prompt_sha,
                usage=usage_dict(response.usage),
                response_model=response.model,
                response_id=response.id,
                stop_reason=response.stop_reason,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            return response

    def record_replay(
        self, *, purpose: str, prompt_version: str, prompt_sha: str, usage: dict[str, int], request_id: str | None = None
    ) -> None:
        """Record a call whose result was replayed from a higher-level cache (e.g. a whole agent conversation)."""
        with self._run.tracer.span(f"llm.{purpose}", request_id=request_id, **self._request_attributes(purpose)) as span:
            self._record(span, purpose=purpose, prompt_version=prompt_version, prompt_sha=prompt_sha, usage=usage, cached_replay=True)

    def record_batch_call(
        self,
        *,
        purpose: str,
        prompt_version: str,
        prompt_sha: str,
        usage: dict[str, int],
        response_model: str | None,
        response_id: str | None,
        stop_reason: str | None,
        request_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record one billed Message Batches result (priced at the batch discount)."""
        with self._run.tracer.span(f"llm.{purpose}", request_id=request_id, **self._request_attributes(purpose)) as span:
            self._record(
                span,
                purpose=purpose,
                prompt_version=prompt_version,
                prompt_sha=prompt_sha,
                usage=usage,
                response_model=response_model,
                response_id=response_id,
                stop_reason=stop_reason,
                error=error,
                batch=True,
            )

    def _write_cache(
        self, cache_path: Path, parsed: BaseModel, usage: dict[str, int], response_model: str, response_id: str, stop_reason: str
    ) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            canonical_json(
                {
                    "output": parsed.model_dump(mode="json"),
                    "usage": usage,
                    "response_model": response_model,
                    "response_id": response_id,
                    "stop_reason": stop_reason,
                }
            ),
            encoding="utf-8",
        )

    def _request_attributes(self, purpose: str) -> dict[str, Any]:
        return {
            "gen_ai.system": "anthropic",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": self.model,
            "llm.purpose": purpose,
        }

    def _record(
        self,
        span: SpanHandle,
        *,
        purpose: str,
        prompt_version: str,
        prompt_sha: str,
        usage: dict[str, int] | None = None,
        response_model: str | None = None,
        response_id: str | None = None,
        stop_reason: str | None = None,
        latency_ms: float = 0.0,
        cached_replay: bool = False,
        error: str | None = None,
        batch: bool = False,
    ) -> LLMCallRecord:
        usage = usage or dict.fromkeys(_USAGE_FIELDS, 0)
        record = LLMCallRecord(
            timestamp=datetime.now(UTC),
            run_id=self._run.run_id,
            trace_id=span.trace_id,
            span_id=span.span_id,
            request_id=span.request_id,
            purpose=purpose,
            model=self.model,
            response_model=response_model,
            response_id=response_id,
            stop_reason=stop_reason,
            latency_ms=latency_ms,
            cost_usd=estimate_cost_usd(self.model, **usage, batch=batch),
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha,
            cached_replay=cached_replay,
            batch=batch,
            error=error,
            **usage,
        )
        self._run.llm_calls.write(record)
        span.set_attributes(
            **{
                "gen_ai.response.model": response_model,
                "gen_ai.response.id": response_id,
                "gen_ai.response.finish_reasons": [stop_reason] if stop_reason else [],
                "gen_ai.usage.input_tokens": record.input_tokens,
                "gen_ai.usage.output_tokens": record.output_tokens,
                "gen_ai.usage.cache_creation_input_tokens": record.cache_creation_input_tokens,
                "gen_ai.usage.cache_read_input_tokens": record.cache_read_input_tokens,
                "llm.cost_usd": record.cost_usd,
                "llm.cached_replay": cached_replay,
                "llm.batch": batch,
            }
        )
        log = logger.error if error else logger.info
        log(
            "llm call %s",
            purpose,
            extra={
                "fields": {
                    "tokens_in": record.input_tokens,
                    "tokens_out": record.output_tokens,
                    "cache_read": record.cache_read_input_tokens,
                    "cost_usd": record.cost_usd,
                    "cached_replay": cached_replay,
                    "batch": batch,
                    "error": error,
                }
            },
        )
        return record
