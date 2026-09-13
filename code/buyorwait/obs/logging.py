"""Structured JSON logging to runs/<run_id>/app.jsonl plus a readable console stream."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .redaction import redact
from .tracing import current_span

ROOT_LOGGER = "buyorwait"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        span = current_span()
        if span is not None:
            payload.update(trace_id=span.trace_id, span_id=span.span_id, request_id=span.request_id)
        fields = getattr(record, "fields", None)
        if fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, sort_keys=True))


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(run_dir: Path, level: int = logging.INFO) -> None:
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(run_dir / "app.jsonl", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")
