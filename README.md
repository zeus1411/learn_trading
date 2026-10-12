# XAUUSD Intraday Edge Research

This repository implements the staged, no-lookahead research workflow described
in the project brief. Research stages are deliberately gated. Stage 0 and Stage
1 are implemented; evaluation and later stages remain blocked.

## Local setup

The repository intentionally does not contain the market-data CSV files. Put the
active normalized BID file at
`data/XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv`; its SHA-256 must match the value
frozen in `config/research.json`.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
```

The current reproducible environment is Python 3.8.10 with Matplotlib 3.7.5.
All generated reports are research artifacts, not financial advice.

## Stage 0

Run data validation and the required causal-signal test:

```powershell
python run.py --stage data --data data\xauusd_m15.csv
```

CSV timestamps must include a UTC offset (`Z` or `+00:00`).  If the source is
known to be UTC but stores naive timestamps, use the explicit, logged
correction:

```powershell
python run.py --stage data --data data\xauusd_m15.csv --assume-utc
```

Outputs are written to `reports/data_quality.json`,
`reports/corrections.jsonl`, and `reports/stage0_run.json`.  The command exits
non-zero for conflicting duplicates, malformed/bad OHLC ticks, insufficient
history, or a failed no-lookahead test.  Gaps and weekend observations are
reported, never silently filled or dropped.

Run deterministic tests from one command:

```powershell
python -m unittest discover -s tests -v
```

For the current HistData source, rebuild normalized M15 UTC data, reconstruct
missing 2023 timestamps from the frozen repair file, and run Stage 0:

```powershell
python src\convert_m1_to_m15.py
python src\reconstruct_2023.py
python run.py --stage data --data .\data\XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv
```

The reconstruction never overwrites a HistData bar. It inserts only timestamps
that are absent in 2023, using the BID OHLC fields from the downloaded EV
Trading Labs annual M15 file (derived from Dukascopy ticks). Inserted rows are
identified by the `source` column and have blank `m1_count`, because the repair
source is already aggregated to M15. See
`reports/2023_reconstruction.json` for hashes, compatibility gates, counts, and
price-difference diagnostics.

## Frozen Stage 0 assumptions

- Input timestamps identify M15 bar **opens** in UTC.
- The current raw source is HistData MetaTrader M1 BID data. Although the
  provider's general specification says fixed EST, this downloaded XAUUSD set
  was empirically verified against Dukascopy-derived BID M15 prices for
  2022-2024. It uses UTC-05 in winter and UTC-04 between the last Sundays of
  March and October (European DST dates), not standard `America/New_York` DST.
  The rule is explicit and regression-tested rather than inferred at runtime.
- The base normalized file is `data/XAUUSD_M15_UTC.csv`. The active research
  file is `data/XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv`, containing 2,944 added
  2023 bars. Volume is omitted because the research brief prohibits its use.
- H1/H4 bars are aligned to UTC clock boundaries.
- HTF values are shifted by a complete HTF bucket before joining to M15.  Thus
  no observation from the current H1/H4 bucket is available to a signal.
- ATR and ADX use Wilder smoothing; EMA uses `adjust=False` recurrence seeded
  by the first observation.
- Exact duplicate rows are removed and logged. Conflicting duplicate
  timestamps are fatal. Out-of-order input is sorted and logged.
- Pivot ties do not form a pivot (the center must be strictly above/below every
  other bar in its window).
- The location/session bucketing definitions are deferred to their specified
  stages and are not used in Stage 0.

## Stage 1

Stage 1 is restricted to the frozen train and validation partitions. The locked
test partition starts at `2025-05-28T09:30:00+00:00`; no test-period signal or
trade result is generated.

```powershell
python run.py --stage signals
python run.py --stage label
```

The signals command reruns the 50-timestamp no-lookahead test on the real Setup
A and B functions, including confirmed pivots, previous-closed H1 features,
previous-closed H4 mapping, and the causal gap filter. The label command applies
the BID-side cost model under normal, +50%, and +100% cost scenarios, writes the
trade and skip logs, and creates 20 deterministic chart examples for each setup.
Setup A charts include at least 60 pre-signal bars, EMA20, rolling 20-bar high
and low, and the previous closed H1 EMA50/EMA200/ADX values.
Setup B charts include at least 80 pre-signal bars, a horizontal labeled pivot
line, and a highlighted sweep bar. `reports/STAGE1_SETUP_B_PIVOT_AUDIT.md`
documents the strict 5/5 audit and prior-close-beyond-pivot diagnostic.

Stage 1 outputs:

- `reports/stage1_signals.json` and `reports/signals_stage1.csv`
- `reports/stage1_label.json`, `reports/trades_stage1.csv`, and
  `reports/skipped_signals_stage1.csv`
- `reports/trades_stage1_cost_plus_50_percent.csv` and
  `reports/trades_stage1_cost_plus_100_percent.csv` (with matching skip logs)
- `reports/charts/setup_a` and `reports/charts/setup_b`
- `reports/STAGE1_SUMMARY.md`

Gap rules are frozen in `config/research.json`: an unexpected gap in the prior
100 M15 bars suppresses a signal; an open trade reaching any discontinuity
closes on the last contiguous bar as `TIMEOUT`. No missing bars are synthesized.
The trade output records whether a trade was truncated and classifies the next
gap as weekend, scheduled daily metals break, scheduled holiday break, or
unexpected. Stage 2 must report all metrics both with and without truncated
trades.

The source field is preserved from input bar through signals and labeled trades.
Long entry uses ask (`bid + spread`) plus adverse slippage; long barriers use
bid. Short entry uses bid minus adverse slippage; short barriers and exits use
synthetic ask (`bid + spread`).

## Stage 2 gate (pre-registered)

Stage 2 remains blocked until the 40 Stage 1 charts have been reviewed and the
user explicitly approves Stage 1. The sample-size rule is not reinterpreted
after results are seen: each setup must have at least 300 labeled trades on the
validation partition and at least 300 in the concatenated walk-forward
out-of-sample windows. Stage 3 later requires at least 100 test trades per
setup. Metrics are also reported per direction, but the GO/NO-GO decision is
made per setup, exactly as specified in the project brief.

