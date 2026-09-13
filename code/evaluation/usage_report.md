# Token Usage Report

- Run ID: `final-3`
- Generated: 2026-09-13 10:19 UTC
- Requests processed: 250
- Providers: anthropic
- Models: claude-sonnet-5
- Live model calls: 286 (0 failed); replayed from cache: 0

## Overall (live calls)

| Metric | Value |
|---|---|
| Model calls | 286 |
| Input tokens (uncached) | 927,632 |
| Cache write input tokens | 0 |
| Cache read input tokens | 0 |
| Output tokens | 85,554 |
| Total tokens | 1,013,186 |
| Average tokens per request | 4,052.7 |
| Estimated total cost (USD) | $2.7108 |
| Estimated cost per request (USD) | $0.010843 |

## Per model

| Provider | Model | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|---|
| anthropic | claude-sonnet-5 | 286 | 927,632 | 0 | 0 | 85,554 | 1,013,186 | $2.7108 |

## Per purpose

| Purpose | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|
| agent.turn | 39 | 137,342 | 0 | 0 | 21,467 | 158,809 | $0.4894 |
| evidence.image | 32 | 100,578 | 0 | 0 | 9,993 | 110,571 | $0.3011 |
| evidence.message | 215 | 689,712 | 0 | 0 | 54,094 | 743,806 | $1.9204 |

## Pricing assumptions

Anthropic first-party list prices, USD per million tokens. Cache writes are billed at 1.25x input and cache reads at 0.1x input.

| Model | Input | Output |
|---|---|---|
| claude-sonnet-5 | $2.00 | $10.00 |
| claude-opus-5 | $5.00 | $25.00 |
| claude-haiku-4-5 | $1.00 | $5.00 |
