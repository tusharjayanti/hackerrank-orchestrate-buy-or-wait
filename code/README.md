# Buy or Wait? — financial decision agent

For every request in `dataset/requests.csv` the system decides whether the user should pay in full, pay partially,
use installments, wait, or not proceed, and writes `output.csv` in the repository root.

Full design: [`DESIGN.md`](DESIGN.md).

## Setup

Requires Python 3.12.

```bash
# from the repository root
uv venv .venv --python 3.12                                   # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r code/requirements.txt   # or: .venv/bin/pip install -r code/requirements.txt
cp code/.env.example .env                                      # then set ANTHROPIC_API_KEY in .env
```

The API key is read only from the environment / `.env` (never committed). Model: `claude-sonnet-5` (override with `MODEL`).

## Run

```bash
.venv/bin/python code/main.py --fresh --run-id final   # full run: evidence + engine + agent -> output.csv
.venv/bin/python code/main.py                          # same, reusing cached LLM results (free, deterministic)
.venv/bin/python code/main.py --mode engine            # engine + evidence, template explanations
.venv/bin/python code/main.py --no-evidence --mode engine   # no LLM calls at all
.venv/bin/python code/main.py --stage ingest           # load data and run input guardrails only
```

Each run writes `runs/<run_id>/`: `app.jsonl` (logs), `spans.jsonl` (OpenTelemetry-shaped traces),
`llm_calls.jsonl` (model, tokens, cost per call), `tool_calls.jsonl`, `evidence.jsonl`, `guardrails.jsonl`,
`traces/<request_id>.json` (full decision trace) and `usage_report.md`.
`evaluation/usage_report.md` is the report of the final full-dataset run.

## Evaluate

```bash
.venv/bin/python code/evaluation/main.py --suite samples --evidence            # S1: score the 25 solved samples
.venv/bin/python code/evaluation/main.py --suite samples --evidence --mode agent
.venv/bin/python code/evaluation/main.py --suite invariants                     # S3: contract checks on output.csv
.venv/bin/python code/evaluation/eval_sets.py --set all                         # evidence gold, synthetic, metamorphic, red-team
cd code && ../.venv/bin/pytest -q                                              # unit and integration tests
```

## How it works

| Layer | What it does | LLM? |
|---|---|---|
| Ingestion (`buyorwait/ingest`) | Pydantic models for every CSV, dated FX conversion, lifecycle resolution (cancelled/failed/pending/duplicate/non-cash) into a home-currency ledger; G1 input checks | no |
| Evidence (`buyorwait/evidence`) | Claude structured outputs read each message and each image (twice) into typed facts with enum kinds, verbatim citations and confidence; G2/G3 guardrails validate them; a resolver turns facts into forecast adjustments | yes |
| Engine (`buyorwait/engine`) | Recurring series detection, 90-day cash-flow timeline, safe amount today, earliest safe full-payment date, candidate plans (full, partial, installments, wait, spending changes), spec ranking and status mapping | no |
| Agent (`buyorwait/agent`) | Claude tool-use loop: when data is ambiguous (e.g. a possible duplicate charge) it judges between engine-simulated scenarios using the spec's conflict rules; writes the customer explanation; G5 grounding checks every submission | yes |
| Output (`buyorwait/output`, `guardrails/contract.py`) | Sample-style formatting, G4 contract validation of every row, safe fallback to the engine row | no |

The engine computes every number; the LLM never does arithmetic. Every LLM result is validated before use and falls
back to deterministic behaviour on failure.

### Guardrails

- **G1** input integrity (row validation, IDs, blank amounts resolvable, FX available)
- **G2** untrusted content: source-authority matrix (e.g. only employers can change salary), injection/scam scanner
- **G3** evidence grounding: verbatim quotes, amounts and dates present in the source, confidence label/score agreement, two image reads must agree (else safer amount)
- **G4** output contract (bounds, status/method pairing, partial and installment rules, spending-change permissions)
- **G5** agent grounding: only numbers present in the context, known citations, explanation consistent with the plan, bounded repairs
- **G9** output completeness (250 rows, exact columns)
