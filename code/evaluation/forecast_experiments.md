# Forecast experiments: variable-spend models for `amount_safe_to_pay`

Goal: raise exact (±0.01) matches of `amount_safe_to_pay` on the 25 solved samples without lowering any other field.
Command: `python code/evaluation/main.py --suite samples --mode engine` (no LLM calls, no evidence).

Gate for adopting a candidate: (a) safe-amount exact goes up, (b) no other field goes down, (c) holds on the odd/even
sample hold-out, (d) `pytest -q` passes.

Every candidate is a knob on branch `forecast-experiment` (`variable_model`, `variable_estimator`, `cadence_rounding`
in `buyorwait/engine/knobs.py`), selectable with `--knobs`, defaulting to the current behaviour. Because no candidate
passed the gate, the knobs were not merged; the shipped engine is the baseline row below. The branch also adds
`python code/evaluation/main.py --dump-ledger <user_id>` to print the projected balance path and headroom.

"Variable spend" = debit series whose cadence is shorter than a month (groceries, transport, dining). Monthly items
(rent, utilities, subscriptions, loans) keep the current projection in every candidate.

## Results (2026-09-13, code revision 42d2e64 + experiment knobs)

| Candidate | Knobs | Composite | Safe amount | Status | Method | Plan | Earliest | Changes | Odd | Even | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Baseline (shipped) | median of all history per (category, flexibility); cadence = median gap of last 6; next = last + cadence | 0.740 | **0.160** | 0.840 | 0.880 | 0.840 | 0.840 | 0.880 | 0.744 | 0.736 | reference |
| H1a | `variable_model=category`, `variable_estimator=mean3` | 0.727 | 0.160 | 0.840 | 0.880 | 0.840 | 0.760 | 0.880 | 0.718 | 0.736 | fails (a) no gain, (b) earliest down |
| H1b | `variable_model=category`, `variable_estimator=mean6` | 0.733 | 0.160 | 0.840 | 0.880 | 0.840 | 0.800 | 0.880 | 0.731 | 0.736 | fails (a), (b) |
| H2a | `variable_model=description`, `variable_estimator=mean3` | 0.740 | 0.160 | 0.880 | 0.960 | 0.840 | 0.720 | 0.880 | 0.731 | 0.750 | fails (a); earliest down 0.84 → 0.72 |
| H2b | `variable_model=description`, `variable_estimator=mean6` | 0.740 | 0.160 | 0.880 | 0.960 | 0.840 | 0.720 | 0.880 | 0.731 | 0.750 | fails (a), (b) |
| H3a | `variable_model=weekly`, `variable_estimator=mean_all` | 0.627 | 0.120 | 0.720 | 0.720 | 0.680 | 0.680 | 0.840 | 0.641 | 0.611 | fails everything |
| H3b | `variable_model=weekly`, `variable_estimator=mean6` | 0.600 | 0.120 | 0.680 | 0.680 | 0.640 | 0.640 | 0.840 | 0.641 | 0.556 | fails everything |
| H4 | `variable_model=calendar_replay`, `variable_estimator=mean_all` | 0.653 | 0.120 | 0.840 | 0.840 | 0.720 | 0.560 | 0.840 | 0.744 | 0.556 | fails (a), (b), (c) |
| H5a | H1a + `cadence_rounding=true` (7/14/30) | 0.720 | 0.120 | 0.880 | 0.880 | 0.840 | 0.800 | 0.800 | 0.744 | 0.694 | fails (a), (b) |
| H5b | H1b + `cadence_rounding=true` | 0.720 | 0.120 | 0.880 | 0.880 | 0.840 | 0.800 | 0.800 | 0.744 | 0.694 | fails (a), (b) |

## Fingerprints a correct model must reproduce

| Sample | Key | Baseline | H1b | H2a | H4 | H5b |
|---|---|---|---|---|---|---|
| request_06 safe = 603.30 | 603.30 | 517.36 (−85.94) | 521.99 | 569.25 (−34.05) | 523.94 | 504.81 |
| request_07 safe = 87,170.56 | 87,170.56 | 94,960.66 (+7,790) | 95,336.09 | 102,240.88 | 93,093.43 | 86,227.05 (−943.51) |
| request_07 earliest = 2024-10-23 | 10-23 | 10-15 | 10-15 | 10-15 | 11-15 | 10-15 |
| request_21 safe = 1,543.35 | 1,543.35 | 1,574.40 (+31.05) | 1,574.40 | 1,574.40 | 1,549.16 (+5.81) | 1,463.70 |
| request_02 earliest = 2025-09-15 | 09-15 | 10-15 | 10-15 | **09-15** | 10-15 | 10-15 |

No candidate reproduces all four fingerprints. request_07's earliest date (23 October, not a payday) is never
produced: every model puts the earliest date on the 15 October salary, so the key must place a large debit between
15 and 23 October that no history-based projection generates. request_21 needs about USD 132 of dining and transport
spending that every model schedules after the 15 April salary; H4 (calendar replay) comes closest (+5.81) but breaks
the earliest date on 9 other samples.

## Hypotheses

- **H1 (per-category discrete series, mean of last N).** Same structure as the baseline with flexibility merged into
  the category and mean instead of median. Neutral on safe amounts, worse on earliest dates: the mean is pulled up by
  occasional large shops, pushing the full-payment date past the true payday on request_03, request_13 and request_23.
- **H2 (per-description series).** Descriptions rotate (Supermarket basket, Fresh food shop, Local market purchase…),
  so each description recurs about every 30 days and the projection becomes several sparse monthly series. Total
  monthly spend is similar, timing is not; three earliest dates move to the wrong month.
- **H3 (weekly, anchored on the last occurrence).** Grossly over-projects 10- and 14-day categories (a 10-day series
  becomes 3 charges per 3 weeks instead of 2), so safe amounts collapse and request_02 never becomes affordable.
- **H4 (replay last calendar month's days).** Best request_21 fingerprint but the replayed days ignore the true cadence
  at month boundaries (a month with an extra occurrence is replayed every month), moving earliest dates for 7 samples.
- **H5 (cadence rounded to 7/14/30).** 10-day and 21-day series are forced to 7/14 or 30, which either doubles or
  halves their frequency; safe-amount exact drops to 0.120.

## Conclusion

The variable-spend residuals in the key are not reproduced by any occurrence-based model built from history: the
implied pre-payday spend is 0.30, 0.59 and 0.55 of the monthly variable mean for user_06, user_21 and user_07, and the
timing that would explain request_07 (a debit between 15 and 23 October) does not appear in that user's history. The
baseline stays. Earlier searches (200 estimator/window/rounding configurations; 64 under the month-end horizon) reached
the same result.
