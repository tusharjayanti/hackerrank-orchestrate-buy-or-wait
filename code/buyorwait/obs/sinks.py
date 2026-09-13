"""Append-only JSONL sinks, safe to share across threads."""

from __future__ import annotations

import threading
from pathlib import Path

from pydantic import BaseModel

from .redaction import redact


class JsonlSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: BaseModel) -> None:
        line = redact(record.model_dump_json())
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # Span-exporter interface used by Tracer; an OTLP exporter would implement the same method.
    export = write
