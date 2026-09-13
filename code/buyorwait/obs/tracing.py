"""Minimal OpenTelemetry-shaped tracer that exports finished spans to pluggable sinks."""

from __future__ import annotations

import contextvars
import secrets
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from ..schemas.enums import SpanStatus
from ..schemas.obs import SpanRecord

T = TypeVar("T")


class SpanExporter(Protocol):
    def export(self, record: SpanRecord) -> None: ...


@dataclass
class SpanHandle:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    request_id: str | None
    start_time_unix_nano: int
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    status_message: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, **attributes: Any) -> None:
        self.attributes.update(attributes)

    def record_error(self, exc: BaseException) -> None:
        self.status = SpanStatus.ERROR
        self.status_message = f"{type(exc).__name__}: {exc}"

    def finish(self, end_time_unix_nano: int) -> SpanRecord:
        attributes = dict(self.attributes)
        if self.request_id:
            attributes.setdefault("request.id", self.request_id)
        return SpanRecord(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            start_time_unix_nano=self.start_time_unix_nano,
            end_time_unix_nano=end_time_unix_nano,
            status=self.status,
            status_message=self.status_message,
            attributes=attributes,
        )


_current_span: contextvars.ContextVar[SpanHandle | None] = contextvars.ContextVar("current_span", default=None)


def current_span() -> SpanHandle | None:
    return _current_span.get()


class Tracer:
    def __init__(self, exporters: Sequence[SpanExporter], trace_id: str | None = None) -> None:
        self._exporters = list(exporters)
        self.trace_id = trace_id or secrets.token_hex(16)

    @contextmanager
    def span(self, name: str, *, request_id: str | None = None, **attributes: Any) -> Iterator[SpanHandle]:
        parent = _current_span.get()
        handle = SpanHandle(
            trace_id=self.trace_id,
            span_id=secrets.token_hex(8),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            request_id=request_id or (parent.request_id if parent else None),
            start_time_unix_nano=time.time_ns(),
            attributes=dict(attributes),
        )
        token = _current_span.set(handle)
        try:
            yield handle
        except BaseException as exc:
            handle.record_error(exc)
            raise
        finally:
            _current_span.reset(token)
            record = handle.finish(time.time_ns())
            for exporter in self._exporters:
                exporter.export(record)


def submit_in_context(executor: Executor, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
    """Submit work to a thread pool so it inherits the caller's current span."""
    context = contextvars.copy_context()
    return executor.submit(context.run, fn, *args, **kwargs)
