# Token Usage Report

- Run ID: `final-4`
- Generated: 2026-09-13 10:36 UTC
- Requests processed: 250
- Command: `python code/main.py --fresh --run-id final-4`
- Code revision: `d57d581`
- Output produced: `output.csv` (250 rows, sha256 `5adc5c471bc52c4b03d7c8c177ae03d9519820507dbfb5efee6ca809dc8699ff`)
- Providers: anthropic
- Models: claude-sonnet-5
- Live model calls: 290 (0 failed); replayed from cache: 0

## Overall (live calls)

| Metric | Value |
|---|---|
| Model calls | 290 |
| Input tokens (all) | 944,581 |
| Input tokens (uncached) | 944,581 |
| Cache write input tokens | 0 |
| Cache read input tokens | 0 |
| Output tokens | 87,902 |
| Total tokens | 1,032,483 |
| Average tokens per request | 4,129.9 |
| Estimated total cost (USD) | $2.7682 |
| Estimated cost per request (USD) | $0.011073 |

## Per model

| Provider | Model | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|---|
| anthropic | claude-sonnet-5 | 290 | 944,581 | 0 | 0 | 87,902 | 1,032,483 | $2.7682 |

## Per purpose

| Purpose | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|
| agent.turn | 43 | 154,291 | 0 | 0 | 23,870 | 178,161 | $0.5473 |
| evidence.image | 32 | 100,578 | 0 | 0 | 9,959 | 110,537 | $0.3007 |
| evidence.message | 215 | 689,712 | 0 | 0 | 54,073 | 743,785 | $1.9202 |

## Pricing assumptions

Anthropic first-party list prices, USD per million tokens. Cache writes are billed at 1.25x input and cache reads at 0.1x input.

| Model | Input | Output |
|---|---|---|
| claude-sonnet-5 | $2.00 | $10.00 |
| claude-opus-5 | $5.00 | $25.00 |
| claude-haiku-4-5 | $1.00 | $5.00 |
