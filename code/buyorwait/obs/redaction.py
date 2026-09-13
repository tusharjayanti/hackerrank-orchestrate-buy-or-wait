"""Guardrail G7: mask secrets before anything is written to logs or JSONL files."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
_KEY_VALUE = re.compile(r"(?i)(x-api-key|authorization|api[_-]?key)(\"?\s*[:=]\s*\"?)([^\s\",]+)")


def redact(text: str) -> str:
    text = _ANTHROPIC_KEY.sub(REDACTED, text)
    return _KEY_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
