"""Buy or Wait? command-line entry point.

Usage (from the repo root):
    python code/main.py                     # evidence extraction + engine + agent explanations -> output.csv
    python code/main.py --mode engine       # evidence + engine only (template explanations)
    python code/main.py --no-evidence       # skip Claude evidence extraction
    python code/main.py --stage ingest      # load dataset, run input guardrails (G1), summarise the ledger
    python code/main.py --knobs knobs.json  # override engine calibration knobs
    python code/main.py --fresh             # ignore cached LLM results (use for the final submission run)
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from buyorwait.agent.loop import DecisionAgent
from buyorwait.agent.runner import AgentRunner
from buyorwait.config import Settings
from buyorwait.engine.knobs import EngineKnobs
from buyorwait.evals.suites import run_invariants
from buyorwait.evidence.extract import EvidenceExtractor
from buyorwait.guardrails.input_checks import check_dataset
from buyorwait.ingest.lifecycle import build_ledger
from buyorwait.ingest.loaders import load_dataset
from buyorwait.obs.llm_client import LLMClient, LLMError
from buyorwait.obs.logging import configure_logging, get_logger
from buyorwait.obs.run_context import RunContext
from buyorwait.obs.usage_report import build_usage_report, load_llm_calls
from buyorwait.output.writer import write_output
from buyorwait.pipeline import EnginePipeline
from buyorwait.schemas.enums import Severity

logger = get_logger("main")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buy or Wait? financial decision agent")
    parser.add_argument("--stage", choices=["ingest", "decide"], default="decide", help="pipeline stage to run")
    parser.add_argument("--mode", choices=["agent", "engine"], default="agent", help="agent writes explanations and settles ambiguities")
    parser.add_argument("--knobs", type=Path, default=None, help="JSON file with EngineKnobs overrides")
    parser.add_argument("--no-evidence", action="store_true", help="skip Claude evidence extraction")
    parser.add_argument("--fresh", action="store_true", help="bypass the LLM result caches")
    parser.add_argument("--run-id", default=None, help="override the generated run id")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.fresh:
        settings = settings.model_copy(update={"cache_dir": settings.runs_dir / "_fresh_cache" / (args.run_id or "latest")})
    knobs = EngineKnobs.model_validate_json(args.knobs.read_text()) if args.knobs else EngineKnobs()
    run = RunContext.create(settings.runs_dir, args.run_id)
    configure_logging(run.run_dir)
    logger.info("run started", extra={"fields": {"run_id": run.run_id, "stage": args.stage, "mode": args.mode, "model": settings.model}})
    invariant_violations = []
    rows = []
    evidence = None
    agent_results = []
    fallbacks = 0

    with run.tracer.span("run", **{"run.id": run.run_id, "run.stage": args.stage, "run.mode": args.mode}):
        with run.tracer.span("ingest.load"):
            dataset = load_dataset(settings.dataset_dir)
        with run.tracer.span("guardrails.G1") as span:
            violations = check_dataset(dataset)
            span.set_attribute("guardrails.violations", len(violations))
        run.record_violations(violations)
        with run.tracer.span("ingest.ledger") as span:
            ledger = build_ledger(dataset.events, dataset.profiles, dataset.fx)
            treatments = Counter(entry.treatment.value for entries in ledger.values() for entry in entries)
            span.set_attributes(**{f"ledger.{name}": count for name, count in treatments.items()})

        if args.stage == "decide":
            llm = None
            if not args.no_evidence or args.mode == "agent":
                try:
                    llm = LLMClient(settings, run)
                except LLMError as exc:
                    logger.error("LLM features disabled: %s", exc)
            if llm is not None and not args.no_evidence:
                evidence = EvidenceExtractor(llm, settings, dataset, run).extract_all()
            pipeline = EnginePipeline(dataset, knobs, tracer=run.tracer, ledger=ledger, evidence=evidence)
            traces_dir = run.run_dir / "traces"
            traces_dir.mkdir(exist_ok=True)

            if args.mode == "agent" and llm is not None:
                runner = AgentRunner(pipeline, DecisionAgent(llm, settings, run), evidence, settings.concurrency)
                with run.tracer.span("agent.all", **{"requests.count": len(dataset.requests)}):
                    agent_results = runner.run(dataset.requests)
                for result in agent_results:
                    (traces_dir / f"{result.request.request_id}.json").write_text(result.base.decision.model_dump_json(), encoding="utf-8")
                    if result.outcome is not None:
                        (traces_dir / f"{result.request.request_id}.agent.json").write_text(result.outcome.model_dump_json(), encoding="utf-8")
                rows = [result.row for result in agent_results]
                fallbacks = sum(result.fell_back for result in agent_results)
            else:
                with run.tracer.span("decide.all", **{"requests.count": len(dataset.requests)}):
                    for request in dataset.requests:
                        result = pipeline.run_request(request)
                        rows.append(result.row)
                        fallbacks += result.fell_back
                        run.record_violations(result.violations)
                        (traces_dir / f"{request.request_id}.json").write_text(result.decision.model_dump_json(), encoding="utf-8")

            write_output(rows, settings.output_path, dataset.template_request_ids)
            with run.tracer.span("guardrails.G9"):
                invariant_violations = run_invariants(dataset, settings.output_path)
            run.record_violations(invariant_violations)

    report = build_usage_report(load_llm_calls(run.llm_calls.path), run_id=run.run_id, request_count=len(rows))
    (run.run_dir / "usage_report.md").write_text(report, encoding="utf-8")

    severities = Counter(violation.severity.value for violation in violations)
    print(f"Run {run.run_id} -> {run.run_dir}")
    print(
        f"Loaded {len(dataset.profiles)} profiles, {len(dataset.events)} events, {len(dataset.requests)} requests, "
        f"{len(dataset.messages)} messages, {len(dataset.images)} images | G1 violations: {dict(severities) or 'none'}"
    )
    if evidence is not None:
        reviews = evidence.reviews.values()
        print(
            f"Evidence: {len(evidence.reviews)} sources, {sum(len(r.accepted) for r in reviews)} accepted facts, "
            f"{sum(1 for r in reviews for v in r.violations if v.severity is Severity.ERROR)} rejected-fact violations, "
            f"{sum(r.injection_detected for r in reviews)} injection flags, {len(evidence.amount_overrides())} blank amounts filled"
        )
    if agent_results:
        outcomes = [result.outcome for result in agent_results if result.outcome is not None]
        print(
            f"Agent: {sum(o.accepted for o in outcomes)}/{len(agent_results)} accepted, {sum(o.repairs for o in outcomes)} repairs, "
            f"{sum(result.scenario_id != 'base' for result in agent_results)} alternative scenarios chosen, "
            f"{sum(o.cached_replay for o in outcomes)} replayed from cache"
        )
    if args.stage == "decide":
        print(f"Wrote {len(rows)} rows to {settings.output_path} ({args.mode} mode)")
        print("Statuses:", dict(Counter(row.affordability_status.value for row in rows)))
        print("Methods:", dict(Counter(row.recommended_payment_method.value for row in rows)))
        print(f"Fallbacks: {fallbacks} | G9/G4 invariant violations: {len(invariant_violations)} | usage report: {run.run_dir / 'usage_report.md'}")
    return 1 if severities.get(Severity.ERROR.value) or invariant_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
