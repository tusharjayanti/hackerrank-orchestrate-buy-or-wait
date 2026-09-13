# Buy or Wait? — financial decision agent

For every request in `dataset/requests.csv` the system decides whether the user should pay in full, pay partially,
use installments, wait, or not proceed, and writes `output.csv` in the repository root.

Full design: [`DESIGN.md`](DESIGN.md).

## Approach in brief

The hidden ground truth follows deterministic rules, so every number in `output.csv` comes from a pure-Python engine:
a 90-day ledger, `amount_safe_to_pay = clamp(min headroom, 0, requested)`, suffix-min for
`earliest_date_for_full_payment`, candidate plans (full, partial, installments, wait, spending changes) ranked by the
six spec keys. Claude (`claude-sonnet-5`, structured outputs) is used only where input is unstructured — reading the
16 images and 215 messages into typed facts — and, on the requests where the engine found a genuine ambiguity, to
choose between engine-simulated scenarios using the spec's conflict rules. The LLM never does arithmetic; every LLM
result is validated and falls back to deterministic behaviour.

## Setup

Requires Python 3.12.

```bash
# from the repository root
uv venv .venv --python 3.12                                   # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r code/requirements.txt   # or: .venv/bin/pip install -r code/requirements.txt
cp code/.env.example .env                                      # then set ANTHROPIC_API_KEY in .env
```

The API key is read only from the environment / `.env` (never committed). Model: `claude-sonnet-5` (override with `MODEL`).
The decision agent runs only on requests where the engine found an ambiguity to judge; set `AGENT_SCOPE=all` to also
have it write the explanation for every other request.

## Run

```bash
.venv/bin/python code/main.py --fresh --run-id final   # full run: evidence + engine + agent -> output.csv
.venv/bin/python code/main.py --fresh --batch-evidence --run-id final   # same, evidence via the Message Batches API (50% cost)
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

| Suite | Command | What it protects against |
|---|---|---|
| S1 samples | `evaluation/main.py --suite samples` | Field accuracy on the 25 labelled rows: a change that lowers any output field |
| S3 invariants | `evaluation/main.py --suite invariants` | A shipped `output.csv` that breaks the output contract (columns, 250 rows, bounds, plan and spending-change rules) |
| S4 evidence gold | `eval_sets.py --set evidence` | Image and message extraction that drifts from rule-labelled messages and hand-labelled images |
| S2 synthetic + S3b metamorphic | `eval_sets.py --set synthetic`, `--set metamorphic` | Engine errors on hand-computed scenarios; violations of invariances such as "more balance never lowers the safe amount" |
| S5 red team | `eval_sets.py --set redteam` | Injected or fabricated evidence reaching the forecast (14 attacks, plus a decision-invariance check) |
| pytest | `cd code && ../.venv/bin/pytest -q` | Unit and integration regressions across loaders, FX, lifecycle, engine, evidence, agent and eval sets |

Calibration changes are adopted only under the gate in `DESIGN.md` §5.4: tests pass, invariants show no violations,
the S1 composite does not drop, red-team decisions do not flip, and the change holds on an odd/even split of the
samples (fit on one half, checked on the other).

## Results and known limitations

Latest S1 report, evidence mode (`--suite samples --evidence`, scored with the evidence extracted by run `final-5`):

| Field | Exact-match accuracy (25 samples) |
|---|---|
| `affordability_status` | 0.880 |
| `recommended_payment_method` | 0.920 |
| `payment_plan` | 0.880 |
| `earliest_date_for_full_payment` | 0.880 |
| `spending_changes_needed` | 0.880 |
| `amount_safe_to_pay` | 0.160 |
| Composite (mean of the six) | 0.767 |

`amount_safe_to_pay` matches exactly on 4 of 25 samples (16%); its mean relative error is 3.08% of the requested
amount, and 16 of 25 are within 2%. Evidence gold (S4, against the same `final-5` extraction): forecast-affecting
facts precision 0.990, recall 1.000; amounts and dates 1.000; image amounts 14/16 strict (0.875). Red team (S5):
14/14 attacks blocked, 0 decisions changed by injected no-effect facts. Synthetic 12/12; metamorphic 0 violations.

The safe-amount gap is everyday-spending projection: the key reserves groceries, transport and dining amounts that
history only approximates. `evaluation/forecast_experiments.md` records the hypotheses tested against the samples —
H1, one discrete series per category with the mean of the last 3 or 6 occurrences; H2, one series per description;
H3, weekly series anchored on the most recent occurrence; H4, replaying the days-of-month of the most recent calendar
month; H5, H1 with the cadence rounded to 7, 14 or 30 days. None raised the exact-match rate, and each lowered at
least one other field, so none passed the gate; the engine was left unchanged and the log ships in the zip.

## Models used

- Design and planning: Claude Fable 5.1 (claude.ai)
- Build: Claude Code with Claude Opus 5 (1M context) for the build (13 of the 15 commits on the branch, per the
  commit trailers) and Claude Fable 5.1 for the final experiments and packaging (the last 2 commits)
- Runtime: `claude-sonnet-5` — evidence extraction via the Anthropic Message Batches API (247 calls), agent turns
  via the Messages API (45 calls)
- Judge: HackerRank's AI judge (model undisclosed)

## Reproducibility

`output.csv` was produced by run `final-5` (`python code/main.py --fresh --batch-evidence --run-id final-5`) on code
revision `42d2e64`; the submission is packaged from commit `9659244`, which differs from `42d2e64` only in
documentation and `.gitignore`. The run made 292 model calls (247 through the Batch API, 0 failed), used 1,043,244
tokens (954,943 input, 88,301 output; 4,173.0 per request) at an estimated $1.6896 total, $0.006758 per request.
`evaluation/usage_report.md` records the sha256 of that `output.csv`
(`01270e4c4b6ecab6f876d20660af39398ae619118eb8bb5ef5639aebcc4f222e`). Rerunning `python code/main.py` replays the
disk cache and is deterministic; `--fresh` re-queries the model and may differ on rows whose evidence extraction or
agent judgement varies (3 of 250 rows differed between the last two fresh runs). Spec interpretations are listed in
`DESIGN.md` §3 (event status handling, engine rules) and §11 (month-end forecast window, LLM-as-judge readings),
with the locked model and authority decisions in §10.

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
