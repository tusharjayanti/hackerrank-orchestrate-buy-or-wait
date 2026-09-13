"""Eval harness CLI.

Usage (from the repo root):
    python code/evaluation/main.py --suite samples [--evidence] [--knobs knobs.json]  # S1: score the 25 solved samples
    python code/evaluation/main.py --suite invariants [--output output.csv]           # S3: contract + completeness checks
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.config import Settings  # noqa: E402
from buyorwait.engine.knobs import EngineKnobs  # noqa: E402
from buyorwait.evals.scoring import write_report  # noqa: E402
from buyorwait.evals.suites import run_invariants, run_samples  # noqa: E402
from buyorwait.evidence.extract import EvidenceExtractor  # noqa: E402
from buyorwait.ingest.loaders import load_dataset  # noqa: E402
from buyorwait.obs.llm_client import LLMClient  # noqa: E402
from buyorwait.obs.logging import configure_logging  # noqa: E402
from buyorwait.obs.run_context import RunContext  # noqa: E402
from buyorwait.pipeline import EnginePipeline  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buy or Wait? eval harness")
    parser.add_argument("--suite", choices=["samples", "invariants"], default="samples")
    parser.add_argument("--knobs", type=Path, default=None, help="JSON file with EngineKnobs overrides")
    parser.add_argument("--evidence", action="store_true", help="use Claude evidence extraction (disk-cached)")
    parser.add_argument("--output", type=Path, default=None, help="output.csv to check (invariants suite)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--show", type=int, default=40, help="max mismatch lines to print")
    args = parser.parse_args(argv)

    settings = Settings()
    run = RunContext.create(settings.runs_dir, args.run_id and f"eval-{args.run_id}")
    configure_logging(run.run_dir)
    dataset = load_dataset(settings.dataset_dir)

    if args.suite == "invariants":
        violations = run_invariants(dataset, args.output or settings.output_path)
        run.record_violations(violations)
        print(f"Invariants: {len(violations)} violations", dict(Counter(violation.code for violation in violations)))
        return 1 if violations else 0

    knobs = EngineKnobs.model_validate_json(args.knobs.read_text()) if args.knobs else EngineKnobs()
    evidence = EvidenceExtractor(LLMClient(settings, run), settings, dataset, run).extract_all() if args.evidence else None
    report, _ = run_samples(EnginePipeline(dataset, knobs, tracer=run.tracer, evidence=evidence), run.run_id)
    write_report(report, run.run_dir / "eval")
    print(f"Samples composite {report.composite:.3f} | rows fully correct {report.exact_rows}/{len(report.requests)}")
    for field, accuracy in report.field_accuracy.items():
        print(f"  {field:32} {accuracy:.3f}")
    shown = 0
    for evaluation in report.requests:
        for result in evaluation.fields:
            if not result.match and shown < args.show:
                print(f"  x {evaluation.request_id:11} {result.field:31} expected={result.expected!r} actual={result.actual!r}")
                shown += 1
    print(f"Report: {run.run_dir / 'eval' / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
