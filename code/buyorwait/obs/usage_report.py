"""Build evaluation/usage_report.md from a run's llm_calls.jsonl."""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..schemas.obs import LLMCallRecord
from .pricing import CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER, PRICES


def load_llm_calls(path: Path) -> list[LLMCallRecord]:
    if not path.exists():
        return []
    return [LLMCallRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _totals(calls: Sequence[LLMCallRecord]) -> dict[str, float]:
    return {
        "calls": len(calls),
        "input": sum(call.input_tokens for call in calls),
        "cache_write": sum(call.cache_creation_input_tokens for call in calls),
        "cache_read": sum(call.cache_read_input_tokens for call in calls),
        "output": sum(call.output_tokens for call in calls),
        "total": sum(call.total_tokens for call in calls),
        "cost": sum(call.cost_usd or 0.0 for call in calls),
    }


def _grouped_rows(calls: Sequence[LLMCallRecord], key: Callable[[LLMCallRecord], tuple[str, ...]]) -> list[str]:
    groups: dict[tuple[str, ...], list[LLMCallRecord]] = defaultdict(list)
    for call in calls:
        groups[key(call)].append(call)
    rows = []
    for group_key in sorted(groups):
        totals = _totals(groups[group_key])
        cells = [*group_key, f"{totals['calls']:,}", f"{totals['input']:,}", f"{totals['cache_write']:,}",
                 f"{totals['cache_read']:,}", f"{totals['output']:,}", f"{totals['total']:,}", f"${totals['cost']:.4f}"]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _provenance(command: str | None, source_revision: str | None, output_path: Path | None) -> list[str]:
    lines = []
    if command:
        lines.append(f"- Command: `{command}`")
    if source_revision:
        lines.append(f"- Code revision: `{source_revision}`")
    if output_path is not None and output_path.exists():
        with output_path.open(encoding="utf-8", newline="") as handle:
            rows = sum(1 for _ in csv.DictReader(handle))
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        lines.append(f"- Output produced: `{output_path.name}` ({rows} rows, sha256 `{digest}`)")
    return lines


def build_usage_report(
    calls: Sequence[LLMCallRecord],
    *,
    run_id: str,
    request_count: int,
    command: str | None = None,
    source_revision: str | None = None,
    output_path: Path | None = None,
) -> str:
    live = [call for call in calls if not call.cached_replay]
    replayed = [call for call in calls if call.cached_replay]
    failed = [call for call in live if call.error]
    overall = _totals(live)
    per_request = max(request_count, 1)

    lines = [
        "# Token Usage Report",
        "",
        f"- Run ID: `{run_id}`",
        f"- Generated: {datetime.now(UTC):%Y-%m-%d %H:%M UTC}",
        f"- Requests processed: {request_count}",
        *_provenance(command, source_revision, output_path),
        f"- Providers: {', '.join(sorted({call.provider for call in calls})) or 'none'}",
        f"- Models: {', '.join(sorted({call.model for call in calls})) or 'none'}",
        f"- Live model calls: {len(live)} ({len(failed)} failed); replayed from cache: {len(replayed)}",
        "",
        "## Overall (live calls)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Model calls | {overall['calls']:,} |",
        f"| Input tokens (all) | {overall['input'] + overall['cache_write'] + overall['cache_read']:,} |",
        f"| Input tokens (uncached) | {overall['input']:,} |",
        f"| Cache write input tokens | {overall['cache_write']:,} |",
        f"| Cache read input tokens | {overall['cache_read']:,} |",
        f"| Output tokens | {overall['output']:,} |",
        f"| Total tokens | {overall['total']:,} |",
        f"| Average tokens per request | {overall['total'] / per_request:,.1f} |",
        f"| Estimated total cost (USD) | ${overall['cost']:.4f} |",
        f"| Estimated cost per request (USD) | ${overall['cost'] / per_request:.6f} |",
        "",
        "## Per model",
        "",
        "| Provider | Model | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |",
        "|---|---|---|---|---|---|---|---|---|",
        *_grouped_rows(live, lambda call: (call.provider, call.model)),
        "",
        "## Per purpose",
        "",
        "| Purpose | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |",
        "|---|---|---|---|---|---|---|---|",
        *_grouped_rows(live, lambda call: (call.purpose,)),
        "",
    ]
    if replayed:
        replay = _totals(replayed)
        lines += [
            "## Replayed from cache (not billed in this run)",
            "",
            f"{replay['calls']:,} calls originally used {replay['total']:,} tokens (about ${replay['cost']:.4f}).",
            "",
        ]
    lines += [
        "## Pricing assumptions",
        "",
        "Anthropic first-party list prices, USD per million tokens. "
        f"Cache writes are billed at {CACHE_WRITE_MULTIPLIER}x input and cache reads at {CACHE_READ_MULTIPLIER}x input.",
        "",
        "| Model | Input | Output |",
        "|---|---|---|",
        *[f"| {model} | ${price.input_per_mtok:.2f} | ${price.output_per_mtok:.2f} |" for model, price in PRICES.items()],
        "",
    ]
    return "\n".join(lines)
