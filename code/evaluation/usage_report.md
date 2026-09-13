# Token Usage Report

- Run ID: `final-5`
- Generated: 2026-09-13 10:47 UTC
- Requests processed: 250
- Command: `python code/main.py --fresh --batch-evidence --run-id final-5`
- Code revision: `42d2e64`
- Output produced: `output.csv` (250 rows, sha256 `01270e4c4b6ecab6f876d20660af39398ae619118eb8bb5ef5639aebcc4f222e`)
- Providers: anthropic
- Models: claude-sonnet-5
- Live model calls: 292 (0 failed); replayed from cache: 0
- Batch API calls: 247 of 292 (billed at 50% of list price)

## Overall (live calls)

| Metric | Value |
|---|---|
| Model calls | 292 |
| Input tokens (all) | 954,943 |
| Input tokens (uncached) | 954,943 |
| Cache write input tokens | 0 |
| Cache read input tokens | 0 |
| Output tokens | 88,301 |
| Total tokens | 1,043,244 |
| Average tokens per request | 4,173.0 |
| Estimated total cost (USD) | $1.6896 |
| Estimated cost per request (USD) | $0.006758 |

## Per model

| Provider | Model | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|---|
| anthropic | claude-sonnet-5 | 292 | 954,943 | 0 | 0 | 88,301 | 1,043,244 | $1.6896 |

## Per purpose

| Purpose | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|
| agent.turn | 45 | 164,653 | 0 | 0 | 25,691 | 190,344 | $0.5862 |
| evidence.image (batch) | 32 | 100,578 | 0 | 0 | 8,720 | 109,298 | $0.1442 |
| evidence.message (batch) | 215 | 689,712 | 0 | 0 | 53,890 | 743,602 | $0.9592 |

## Pricing assumptions

Anthropic first-party list prices, USD per million tokens. Cache writes are billed at 1.25x input and cache reads at 0.1x input; Message Batches API calls at 50% of these prices.

| Model | Input | Output |
|---|---|---|
| claude-sonnet-5 | $2.00 | $10.00 |
| claude-opus-5 | $5.00 | $25.00 |
| claude-haiku-4-5 | $1.00 | $5.00 |
