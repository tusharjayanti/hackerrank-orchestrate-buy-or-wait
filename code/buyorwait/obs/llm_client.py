"""The only module that calls the Anthropic API: tracing, token accounting and result caching."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import Settings
from ..schemas.obs import LLMCallRecord
from .logging import get_logger
from .pricing import estimate_cost_usd
from .run_context import RunContext
from .tracing import SpanHandle

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
    ) -> OutputModel:
        """Structured-output call validated against `output_model`, cached on disk by prompt hash."""
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
        cache_path = self._cache_dir / f"{prompt_sha}.json"

        with self._run.tracer.span(f"llm.{purpose}", request_id=request_id, **self._request_attributes(purpose)) as span:
            if use_cache and cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
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

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                canonical_json(
                    {
                        "output": parsed.model_dump(mode="json"),
                        "usage": usage,
                        "response_model": response.model,
                        "response_id": response.id,
                        "stop_reason": response.stop_reason,
                    }
                ),
                encoding="utf-8",
            )
            return parsed

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
            cost_usd=estimate_cost_usd(self.model, **usage),
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha,
            cached_replay=cached_replay,
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
                    "error": error,
                }
            },
        )
        return record
