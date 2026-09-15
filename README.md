# Buy or Wait? — financial decision agent

My solution to **Buy or Wait?**, the challenge in the HackerRank Orchestrate 24-hour hackathon (September 2026).

For each purchase request, the agent decides whether the user should pay in full, pay partially, use installments,
wait, or not proceed. It then writes `output.csv` with a safe amount, a payment plan, the earliest safe
full-payment date, any spending changes, and a grounded explanation.

> The organizer's original README (challenge brief, dataset layout, output contract, submission rules) is kept as
> [`HACKERRANK_README.md`](HACKERRANK_README.md). Upstream version:
> [interviewstreet/hackerrank-orchestrate-september26](https://github.com/interviewstreet/hackerrank-orchestrate-september26/blob/main/README.md).
> The full spec is in [`problem_statement.md`](problem_statement.md).

## Result

🏆 **Rank #178 of 3,062 participants** (top 6%).

Scores on the 25 public solved samples (exact match, evidence mode):

| Field | Accuracy |
|---|---|
| `affordability_status` | 0.880 |
| `recommended_payment_method` | 0.920 |
| `payment_plan` | 0.880 |
| `earliest_date_for_full_payment` | 0.880 |
| `spending_changes_needed` | 0.880 |
| `amount_safe_to_pay` | 0.160 (mean error 3.08% of the requested amount; 16/25 within 2%) |
| **Composite** | **0.767** |

The full 250-request run made 292 model calls and used about 1.04M tokens, for an estimated **$1.69** ($0.0068 per
request).

And then there was the AI interview: my laptop froze in the middle of it 😂

## Approach

The hidden ground truth follows deterministic rules, so **every number comes from code and never from the LLM**.
Claude (`claude-sonnet-5`) is used only where the input is unstructured or ambiguous.

```
dataset/ ─▶ Ingestion   Pydantic models, dated FX, lifecycle resolution (cancelled / failed / pending / duplicates)
         ─▶ Evidence    Claude structured outputs read 215 messages and 16 images into typed, cited facts
         ─▶ Engine      recurrence detection, 90-day cash-flow ledger, safe amount, earliest date, ranked plans
         ─▶ Agent       Claude tool use, only on ambiguous requests: picks between engine-simulated scenarios
         ─▶ Guardrails  contract + grounding checks; any failure falls back to the deterministic engine row
         ─▶ output.csv  + runs/<run_id>/ logs, traces, per-call token usage, usage report
```

- **Safe amount:** `clamp(min future headroom above minimum balance, 0, requested)`. The earliest full-payment date
  comes from a suffix minimum over the projected ledger.
- **Plans:** full, partial, each permitted installment option, wait, and each of these combined with up to three
  spending changes. Plans are ranked by the spec's six tie-break keys.
- **Untrusted evidence:** a source-authority matrix (for example, only an employer can change salary), an
  injection and scam scanner, verbatim-citation checks, and two image reads that must agree.
- **Cost:** evidence extraction runs through the Message Batches API at 50% of list price, and the agent runs only
  on the 32 of 250 requests where the engine flagged a real ambiguity.
- **Evaluation:** sample scoring, output-contract invariants, evidence gold labels, synthetic and metamorphic tests,
  and a red-team set of 14 injection attacks, all blocked.

Details: [`code/README.md`](code/README.md) (setup, run, evaluate, results, reproducibility) and
[`code/DESIGN.md`](code/DESIGN.md) (full system design).

## Quick start

Requires Python 3.12 and an `ANTHROPIC_API_KEY`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r code/requirements.txt
cp code/.env.example .env        # set ANTHROPIC_API_KEY

.venv/bin/python code/main.py --no-evidence --mode engine   # deterministic engine only, no LLM calls
.venv/bin/python code/main.py --fresh --batch-evidence      # full pipeline -> output.csv
cd code && ../.venv/bin/pytest -q                            # tests
```

## Repository layout

```text
code/                  Solution: buyorwait/ package, main.py, evaluation/, tests/, DESIGN.md
dataset/               Challenge inputs (provided by HackerRank)
problem_statement.md   Challenge spec (provided by HackerRank)
HACKERRANK_README.md   Organizer's original README
AGENTS.md              AI coding agent rules used during the hackathon
```

## Known limitation

`amount_safe_to_pay` is exact on only 16% of the samples. The gap comes from projecting everyday spending (groceries,
transport, dining) that the history only approximates. Five forecasting hypotheses were tested against the samples,
and none passed the adoption gate; see [`code/evaluation/forecast_experiments.md`](code/evaluation/forecast_experiments.md).
