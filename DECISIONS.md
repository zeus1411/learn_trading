# Research decisions

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
