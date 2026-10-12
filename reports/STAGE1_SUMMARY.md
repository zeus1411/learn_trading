# Stage 1 checkpoint - signals and labeling

Status: **PASS / MANUAL CHART REVIEW REQUIRED**

Stage 1 used only the frozen train and validation partitions. It did not create
signals, labels, or metrics for the locked test period.
No Setup A/Setup B parameter or GO/NO-GO gate was changed in this regeneration.

## Preconditions

- Active dataset SHA-256:
  `99654161cc09ab9336e20063666b467250b0181151dab366ed1ead162e68d3f6`
- Timezone verification: PASS for every available ISO week in 2020-2026.
- Weeks where a nonzero timestamp shift matched better: 0.
- The 2026 late-October/early-November focus weeks are unavailable because the
  dataset ends on 2026-10-02.
- Unit and integration tests after artifact verification: 25/25 PASS.

## Locked chronological split

| Partition | UTC range | Rows |
|---|---|---:|
| Train | 2020-01-01 23:00 to 2024-01-22 10:00 | 95,682 |
| Validation | 2024-01-22 10:15 to 2025-05-28 09:15 | 31,894 |
| Test - locked | 2025-05-28 09:30 to 2026-10-02 20:45 | 31,895 |

Test partition SHA-256:
`1a96e7873bd39b43f2570d6e8b810a0e2b83422878e19a953cbfb4e6c7f89fec`

## No-lookahead

Stage 0 used `src.pipeline.generate_all_signals`, which directly called the real
Setup A and Setup B generators. Stage 1 reran the same public signal function
after adding the frozen causal gap policy.

Coverage in the Stage 1 run:

- Setup A real signal logic
- Setup B real signal logic and right-delayed confirmed pivots
- Previous complete H1 EMA50, EMA200, and ADX values
- Previous complete H4 bar mapping (verified although H4 is not a setup rule)
- Prior-100-M15-bar unexpected-gap filter

Result: **PASS at 50/50 deterministic timestamps**.

## Signal counts - train plus validation only

| Setup | Long | Short | Total |
|---|---:|---:|---:|
| A | 4,745 | 3,104 | 7,849 |
| B | 2,154 | 2,114 | 4,268 |
| Total | 6,899 | 5,218 | 12,117 |

The unexpected-gap lookback suppressed 27 raw signals: 14 A-short, 6 B-long,
and 7 B-short. Four unexpected gap events were observed in the development
partition. The `source` field remains present in the signal output.

## Labeling audit

- Input signals: 12,117
- Labeled trades: 6,050
- Skipped signals: 6,067
- Position-already-open skips: 4,968
- Risk above 3 ATR: 883
- Risk below 0.8 ATR: 114
- Entry crosses a gap: 100
- Incomplete development horizon: 2
- Trades closed before a gap/weekend as TIMEOUT: 1,025
- Truncated by weekend/weekend crossing: 211
- Truncated by scheduled daily metals break: 785
- Truncated by scheduled holiday break: 29
- Truncated by unexpected gap: 0
- Same-bar SL and TP counted as LOSS: 11

These are implementation audit counts, not an edge evaluation. Expectancy,
profit factor, confidence intervals, gates, and test results have not been run.

## BID execution model

- Spread: 0.30 USD
- Slippage: 0.10 USD per trade, applied adversely at entry
- Long entry: BID open + spread + slippage
- Long SL, TP, and exit: BID OHLC
- Short entry: BID open - slippage
- Short SL, TP, and exit: ASK OHLC, synthesized as BID + spread

The signal, entry, and exit source fields are retained in the labeled-trade CSV.

## Cost scenarios

Labeling is rerun independently for each frozen cost multiplier. This is not a
post-hoc subtraction from `result_r`: entry, risk, TP, barrier hits, ATR risk
filters, position overlap, and therefore the accepted trade set are recomputed.

| Scenario | Multiplier | Labeled trades | Skipped | Truncated |
|---|---:|---:|---:|---:|
| Normal | 1.0x | 6,050 | 6,067 | 1,025 |
| Costs +50% | 1.5x | 6,003 | 6,114 | 1,020 |
| Costs +100% | 2.0x | 5,979 | 6,138 | 1,014 |

Stage 2 is required to report every metric for both views: including truncated
trades and excluding all trades where `truncated_by_gap` is true.

## Manual charts

- 20 deterministic examples: `reports/charts/setup_a`
- 20 deterministic examples: `reports/charts/setup_b`
- Contact sheets: `reports/charts/setup_a_contact_sheet.png` and
  `reports/charts/setup_b_contact_sheet.png`

Every Setup A chart now shows at least 60 M15 bars before the signal, EMA20,
the rolling 20-bar highest high and lowest low, and the previous closed H1
EMA50/EMA200/ADX14 values used at the signal bar.

Purple shading identifies bars inserted from the Dukascopy-derived repair
source. Charts display BID candles and state the BID/ASK execution convention.

## Gate

Stop here. Stage 2 evaluation and walk-forward remain blocked until the user
reviews the 40 examples and explicitly approves Stage 1.
