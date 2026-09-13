"""Extra eval sets CLI (no API calls).

Usage (from the repo root):
    python code/evaluation/eval_sets.py --set all
    python code/evaluation/eval_sets.py --set evidence [--evidence-file runs/<run>/evidence.jsonl] [--regenerate-gold]
    python code/evaluation/eval_sets.py --set synthetic|metamorphic|redteam
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buyorwait.config import REPO_ROOT, Settings  # noqa: E402
from buyorwait.evals.evidence_gold import (  # noqa: E402
    build_message_gold,
    load_image_gold,
    load_message_gold,
    load_reviews,
    render_evidence_report,
    save_models,
    score_evidence,
)
from buyorwait.evals.metamorphic import render_metamorphic_report, run_metamorphic  # noqa: E402
from buyorwait.evals.redteam import render_redteam_report, run_redteam  # noqa: E402
from buyorwait.evals.synthetic import render_synthetic_report, run_synthetic  # noqa: E402
from buyorwait.ingest.loaders import load_dataset  # noqa: E402
from buyorwait.obs.logging import configure_logging  # noqa: E402
from buyorwait.obs.run_context import RunContext  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MESSAGE_GOLD = FIXTURES / "message_gold.json"
IMAGE_GOLD = FIXTURES / "image_gold.json"
DEFAULT_EVIDENCE = REPO_ROOT / "runs" / "eval-p3-evidence" / "evidence.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buy or Wait? extra eval sets")
    parser.add_argument("--set", choices=["evidence", "synthetic", "metamorphic", "redteam", "all"], default="all")
    parser.add_argument("--evidence-file", type=Path, default=DEFAULT_EVIDENCE, help="evidence.jsonl from a run with extraction")
    parser.add_argument("--regenerate-gold", action="store_true", help="rebuild message_gold.json from the rule labeler")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    run = RunContext.create(settings.runs_dir, f"evalsets-{args.run_id}" if args.run_id else None)
    configure_logging(run.run_dir)
    out_dir = run.run_dir / "eval_sets"
    out_dir.mkdir(exist_ok=True)
    dataset = load_dataset(settings.dataset_dir)
    selected = {"evidence", "synthetic", "metamorphic", "redteam"} if args.set == "all" else {args.set}
    failed = False

    if "evidence" in selected:
        if args.regenerate_gold or not MESSAGE_GOLD.exists():
            save_models(build_message_gold(dataset.messages), MESSAGE_GOLD)
        report = score_evidence(load_message_gold(MESSAGE_GOLD), load_image_gold(IMAGE_GOLD), load_reviews(args.evidence_file))
        (out_dir / "evidence_gold.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "evidence_gold.md").write_text(render_evidence_report(report), encoding="utf-8")
        print(
            f"S4 evidence: forecast facts P={report.forecast_precision:.3f} R={report.forecast_recall:.3f} | amounts {report.amount_match_rate:.3f} "
            f"| dates {report.date_match_rate:.3f} | images strict {report.image_accuracy_strict:.3f} lenient {report.image_accuracy_lenient:.3f} "
            f"| unmatched messages {len(report.unmatched_messages)} | disagreements {len(report.disagreements)}"
        )

    if "synthetic" in selected:
        report = run_synthetic()
        (out_dir / "synthetic.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "synthetic.md").write_text(render_synthetic_report(report), encoding="utf-8")
        print(f"S2 synthetic: {report.passed}/{report.cases} cases pass")
        failed |= report.passed != report.cases

    if "metamorphic" in selected:
        report = run_metamorphic(dataset)
        (out_dir / "metamorphic.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "metamorphic.md").write_text(render_metamorphic_report(report), encoding="utf-8")
        print("S3b metamorphic: " + ", ".join(f"{r.relation}={len(r.violations)}" for r in report.relations))
        failed |= any(r.violations for r in report.relations)

    if "redteam" in selected:
        report = run_redteam(dataset)
        (out_dir / "redteam.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "redteam.md").write_text(render_redteam_report(report), encoding="utf-8")
        changed = len(report.invariance.changed_rows) if report.invariance else 0
        print(f"S5 red team: {report.blocked}/{report.attacks} attacks blocked | rows changed by no-effect facts: {changed}")
        failed |= report.blocked != report.attacks or changed > 0

    print(f"Reports: {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
