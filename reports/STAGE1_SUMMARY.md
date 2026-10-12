# Stage 1 checkpoint - signals and labeling

Status: **PASS / MANUAL CHART REVIEW REQUIRED**

Stage 1 used only the frozen train and validation partitions. It did not create
signals, labels, or metrics for the locked test period. No numeric Setup A or
Setup B parameter and no GO/NO-GO gate was changed. Setup B is now versioned;
the active implementation is B_v2 as recorded in `DECISIONS.md`.

## Preconditions

- Active dataset SHA-256:
  `99654161cc09ab9336e20063666b467250b0181151dab366ed1ead162e68d3f6`
- Timezone verification: PASS for every available ISO week in 2020-2026.
- Unit and integration tests after artifact verification: 31/31 PASS.
- Test begins at `2025-05-28T09:30:00+00:00`, remains locked, and was not used
  to create signal or trade results.

## No-lookahead

The active B_v2 implementation and unchanged Setup A passed the mandatory
full-history versus prefix comparison at 50/50 deterministic timestamps. This
includes delayed pivots, previous-closed H1 features, previous-closed H4
mapping, and the causal unexpected-gap filter.

## Signal counts - train plus validation only

| Setup | Long | Short | Total |
|---|---:|---:|---:|
| A | 4,745 | 3,104 | 7,849 |
| B_v2 | 624 | 598 | 1,222 |
| Total | 5,369 | 3,702 | 9,071 |

The unexpected-gap lookback suppressed 19 raw signals: 14 A-short, 2 B_v2-long,
and 3 B_v2-short. The `source` and `strategy_version` fields remain present in
the signal output.

### B_v2 train/validation counts

| Split | Long signals | Short signals | Total signals | Long trades | Short trades | Total trades |
|---|---:|---:|---:|---:|---:|---:|
| Train | 475 | 444 | 919 | 383 | 377 | 760 |
| Validation | 149 | 154 | 303 | 118 | 130 | 248 |

Trade counts above use normal-cost labeling. No expectancy, profit factor,
confidence interval, or GO/NO-GO metric was computed.

## Labeling audit

- Input signals: 9,071
- Labeled trades: 3,838
- Skipped signals: 5,233
- Position-already-open skips: 4,560
- Risk above 3 ATR: 483
- Risk below 0.8 ATR: 103
- Entry crosses a gap: 86
- Incomplete development horizon: 1
- Trades truncated before a gap/weekend: 658
- Truncated by weekend/weekend crossing: 136
- Truncated by scheduled daily metals break: 506
- Truncated by scheduled holiday break: 16
- Truncated by unexpected gap: 0
- Same-bar SL and TP counted as LOSS: 6

These are implementation counts, not edge results.

## Cost scenarios

Labeling is rerun independently for every cost multiplier. Entry, risk, TP,
barrier hits, risk filters, position overlap, and the accepted trade set are
therefore recomputed rather than adjusted after the fact.

| Scenario | Multiplier | Labeled trades | Skipped | Truncated |
|---|---:|---:|---:|---:|
| Normal | 1.0x | 3,838 | 5,233 | 658 |
| Costs +50% | 1.5x | 3,822 | 5,249 | 654 |
| Costs +100% | 2.0x | 3,823 | 5,248 | 651 |

Stage 2 must report every metric both including and excluding truncated trades.

## B_v1 gap audit

The audit scope is all 4,268 kept B_v1 signals.

| Interval | Any gap | Weekend | Holiday | Daily break | Unexpected |
|---|---:|---:|---:|---:|---:|
| 11-bar pivot window | 546 | 111 | 13 | 422 | 0 |
| Pivot through sweep | 1,047 | 190 | 38 | 819 | 0 |

Counts by type are signal counts. The pivot-to-sweep interval contains 193
weekend gap events across 190 signals. Daily breaks are counted but remain
allowed in B_v2; weekend, holiday, and unexpected gaps are rejected.

## B_v2 rule and pivot audit

B_v1 remains available behind `setup_b.version = "v1"`. Active B_v2 invalidates
a pivot on the first bar that trades beyond it (`low < P` for lows, `high > P`
for highs). Only that same bar may qualify as the sweep and must still meet the
frozen 0.1 ATR buffer plus close-back-inside conditions.

The regenerated audit covers all 1,222 kept B_v2 signals:

- Strict 5-left/5-right violations: 0.
- Prior pierce beyond the pivot before the sweep: 0.
- Prior close beyond the pivot before the sweep: 0.
- No B_v1 expectancy or profit factor was computed.

Trial accounting is now Setup A `n_trials = 1`, Setup B `n_trials = 2`, and
global `n_trials = 3`.

## Manual charts

- Setup A: 20 deterministic examples, unchanged rules and counts.
- Setup B_v2: 20 deterministic examples with at least 80 pre-signal bars, a
  horizontal pivot line labeled with price, and an orange sweep-bar highlight.
- Purple shading identifies bars inserted from the Dukascopy-derived repair
  source. Charts use BID candles and document the BID/ASK convention.

Relevant reports:

- `DECISIONS.md`
- `reports/STAGE1_SETUP_B_V1_GAP_AUDIT.md`
- `reports/STAGE1_SETUP_B_V2_PIVOT_AUDIT.md`
- `reports/STAGE1_SETUP_B_V2_COUNTS.md`
- `reports/stage1_label.json`

## Gate

Stop here. Stage 2 remains blocked until the regenerated B_v2 charts and this
Stage 1 checkpoint receive explicit human approval.
