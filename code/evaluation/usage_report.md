# Token Usage Report

- Run ID: `final-2`
- Generated: 2026-09-13 09:08 UTC
- Requests processed: 250
- Providers: anthropic
- Models: claude-sonnet-5
- Live model calls: 508 (0 failed); replayed from cache: 0

## Overall (live calls)

| Metric | Value |
|---|---|
| Model calls | 508 |
| Input tokens (uncached) | 790,812 |
| Cache write input tokens | 829,669 |
| Cache read input tokens | 36,640 |
| Output tokens | 186,860 |
| Total tokens | 1,843,981 |
| Average tokens per request | 7,375.9 |
| Estimated total cost (USD) | $5.5317 |
| Estimated cost per request (USD) | $0.022127 |

## Per model

| Provider | Model | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|---|
| anthropic | claude-sonnet-5 | 508 | 790,812 | 829,669 | 36,640 | 186,860 | 1,843,981 | $5.5317 |

## Per purpose

| Purpose | Calls | Input | Cache write | Cache read | Output | Total | Est. cost |
|---|---|---|---|---|---|---|---|
| agent.turn | 261 | 522 | 829,669 | 36,640 | 123,135 | 989,966 | $3.3139 |
| evidence.image | 32 | 100,578 | 0 | 0 | 9,620 | 110,198 | $0.2974 |
| evidence.message | 215 | 689,712 | 0 | 0 | 54,105 | 743,817 | $1.9205 |

## Pricing assumptions

Anthropic first-party list prices, USD per million tokens. Cache writes are billed at 1.25x input and cache reads at 0.1x input.

| Model | Input | Output |
|---|---|---|
| claude-sonnet-5 | $2.00 | $10.00 |
| claude-opus-5 | $5.00 | $25.00 |
| claude-haiku-4-5 | $1.00 | $5.00 |
