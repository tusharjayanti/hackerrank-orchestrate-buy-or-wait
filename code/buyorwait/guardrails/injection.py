"""Guardrail G2: flag instruction-like or scam text inside untrusted messages and images (log only)."""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (all|any|previous|prior|the above)",
        r"(system|developer) prompt",
        r"you are (now )?(an?|the) (ai|assistant|model)",
        r"(mark|classify|approve) (this|the) (request|payment|purchase)",
        r"affordable_now|full_payment|not_recommended",
        r"pay (the )?(release|processing|unlock) (charge|fee)",
        r"bayar biaya (pencairan|pemrosesan)",
        r"(selected|terpilih) (for|untuk) .{0,20}(prize|hadiah)",
    )
]


def scan_for_injection(text: str) -> list[str]:
    return [match.group(0) for pattern in _PATTERNS if (match := pattern.search(text))]
