# Research decisions

## 2026-10-14 - Stage 1 approval and Stage 2 authorization

The user explicitly approved Stage 1 for Setup A and Setup B_v2 after manually
reviewing the charts. This approves implementation correctness, not an edge.
Stage 2 is authorized on the existing 2020-2025 train + validation development
data only. The externally downloaded 2009-2019 data is not loaded into this
project and is outside this cycle.

Before evaluating edge metrics, the frozen gate remains >= 300 labeled
validation trades per setup. B_v2 has 248 normal-cost validation trades and is
therefore INSUFFICIENT_DATA; it cannot PASS this cycle. Do not lower or change
the gate. B_v2 metrics may be reported descriptively. Setup A remains eligible
for evaluation. Setup parameters, costs, split and trial counts remain frozen.

The Stage 2 gates must hold on both validation and concatenated walk-forward
OOS: n >= 300, net expectancy >= +0.10R, lower raw 95% bootstrap CI > 0,
profit factor >= 1.15, positive expectancy in >= 70% of calendar years AND
quarters, and positive expectancy with +50% costs. Raw and Bonferroni-adjusted
inference will be reported for A=1, B=2 and global=3 trials. Decisions are per
setup (directions are descriptive), with both truncation views reported.

Walk-forward uses non-overlapping rolling 3-month blocks beginning at validation
start, advances 3 months, fits nothing and performs no parameter search. Only
complete blocks enter primary concatenated OOS gates; the trailing partial
block is reported separately. Trades crossing an OOS block end are excluded
from that block, with boundary counts disclosed. Sample size is assessed on
concatenated OOS, not individually on each 3-month window.

Housekeeping checks completed before Stage 2 implementation:

- Full initial suite: 31/31 PASS.
- Counts-only audit of all 1,222 kept B_v2 signals, sweep exclusive through
  confirmation inclusive: 6 signals cross a weekend (2 long, 4 short), 0 a
  scheduled holiday, 0 an unexpected gap; union = 6. Separately, 13 signals
  cross a scheduled daily metals break. Signal logic is unchanged.
- Git history contains `.tmp_histdata_cookies.txt` in commits `510cfd0` and
  `5749cd4`. No push is allowed. History cleanup is outside this task.
- `.gitignore` covers `data/`, `rawdata/` and `.tmp_*`; the commented rawdata
  rule was re-enabled. No credential contents were inspected or reproduced.

Stage 3 remains unauthorized; do not run the locked final test.

## 2026-10-14 - Stage 2 outcome

Stage 2 has completed on the approved development artifacts. Setup A is FAIL.
B_v2 remains INSUFFICIENT_DATA; its numerical gate result is also FAIL, reported
descriptively without changing its status or the 300-trade minimum.

Normal costs, combined directions, including truncated trades:

| Setup | Scope | n | Net expectancy R | Raw bootstrap 95% CI | PF |
|---|---|---:|---:|---|---:|
| A | Validation | 694 | -0.000733 | [-0.092924, +0.093026] | 0.998674 |
| A | Complete rolling OOS | 639 | -0.009926 | [-0.104434, +0.084164] | 0.982100 |
| B_v2 | Validation | 248 | -0.206365 | [-0.365199, -0.033475] | 0.672957 |
| B_v2 | Complete rolling OOS | 223 | -0.213436 | [-0.395658, -0.025797] | 0.662981 |

Both setups fail expectancy/PF/calendar/cost gates in both truncation views.
A meets the sample-size minimum; B_v2 does not. Removing gap-truncated trades
does not rescue either result. No parameters or thresholds are changed and no
parameter-change proposal is made. Neither setup advances to the locked test.

The separate integrity checker verified the locked test SHA-256 before and
after Stage 2: `1a96e7873bd39b43f2570d6e8b810a0e2b83422878e19a953cbfb4e6c7f89fec`.
It hashes opaque strings only and returns no test data; evaluation receives
zero test rows. Stage 2 does not load the external 2009-2019 data.

Detailed raw/adjusted inference, all scenarios/directions/views, calendar
breakdowns, rolling windows and equity are in `reports/STAGE2_SUMMARY.md` and
`reports/stage2_results.json`. Stop for human review. Stage 3 has not run.
Push remains blocked by the historical cookie file.

## 2026-10-12 - Setup B_v2

Status: Stage 1 remains unapproved for Setup B. No validation expectancy,
profit factor, or other edge metric has been computed for B_v1.

Decision:

- Preserve B_v1 in `src/signals_b.py` and select the active implementation via
  `config/research.json` (`setup_b.version`).
- Activate B_v2 without changing ATR, pivot, buffer, confirmation, SL, labeling,
  cost, or GO/NO-GO parameters.
- A pivot is invalidated by the first bar that trades beyond it: `low < P` for
  pivot lows or `high > P` for pivot highs. Only that same first-pierce bar may
  qualify as the sweep, and it must also satisfy the frozen 0.1 ATR buffer and
  close-back-inside rule.
- Reject a pivot if its 11-bar pivot window or its pivot-to-sweep interval
  contains a weekend, scheduled holiday, or unexpected gap.
- Daily metals breaks remain allowed in B_v2. The B_v1 audit found 422 signals
  with a daily break in the 11-bar window and 819 with one between pivot and
  sweep; this count is retained explicitly rather than silently changing the
  approved rejection list.

Reason:

- The B_v1 audit found that 2,759 of 4,268 kept signals (64.64%) used a pivot
  after price had already closed beyond it before the selected sweep.
- Pivot windows can straddle weekend and holiday gaps, so the strict 5/5 shape
  alone does not guarantee a continuous local structure.

Trial accounting:

- Setup B `n_trials`: 1 -> 2 (`B_v1`, `B_v2`).
- Global `n_trials`: 2 -> 3 (`A_v1`, `B_v1`, `B_v2`).
- This increment is recorded before any validation edge metric is computed.

Frozen B_v1 gap audit:

| Interval | Any gap | Weekend | Holiday | Daily break | Unexpected |
|---|---:|---:|---:|---:|---:|
| 11-bar pivot window | 546 | 111 | 13 | 422 | 0 |
| Pivot through sweep | 1,047 | 190 | 38 | 819 | 0 |

Counts by gap type are signal counts. There were 193 weekend gap events across
the 190 pivot-to-sweep signals; other type counts equal event counts.
