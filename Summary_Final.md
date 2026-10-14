# XAUUSD Research Pipeline - Project Handoff and Operating Guide

## Status update - 2026-10-14

The original handoff below describes the pre-approval state as of 2026-10-12.
The user has now manually approved Stage 1 for A and B_v2. Stage 2 was
implemented and run on the existing development dataset only: Setup A FAIL;
B_v2 INSUFFICIENT_DATA (248 validation trades vs the unchanged gate of 300).
Read `DECISIONS.md`, `reports/STAGE2_SUMMARY.md` and
`reports/stage2_results.json` for the current outcome. Test hashes matched
before/after; no test results were evaluated. The external 2009-2019 data was
not loaded. Stage 3 remains unrun. Commits are local; push is blocked because
`.tmp_histdata_cookies.txt` occurs in Git history.

Last updated: 2026-10-12

This document is the durable context for continuing work on this repository.
It explains what has been built, why key decisions were made, how to reproduce
the existing results, what must remain frozen, and what is still blocked.

This is a research and validation pipeline, not financial advice. Backtest
results do not guarantee live profitability, and leveraged gold trading can
cause substantial losses.

## 1. Current status at a glance

| Area | Status |
|---|---|
| Data acquisition and normalization | Complete |
| 2023 missing-data reconstruction | Complete |
| Stage 0 data-quality validation | PASS |
| No-lookahead verification | PASS, 50/50 deterministic timestamps |
| Train/validation/test split lock | Complete; test remains untouched |
| Stage 1 Setup A signals and labels | Generated; rules unchanged |
| Stage 1 Setup B_v1 audit | Complete; B_v1 rejected as an implementation candidate |
| Stage 1 Setup B_v2 implementation | Complete |
| Stage 1 B_v2 charts and pivot audit | Generated; manual approval still required |
| Stage 2 evaluation and walk-forward | BLOCKED pending explicit Stage 1 approval |
| Final locked test | Not run |
| Pine Script / alerts / backend | Not started and should remain deferred |

The active Setup B implementation is `B_v2`. The original `B_v1` code is
preserved behind a version flag for reproducibility.

The repository is on branch `main`. The current B_v2 implementation and its
regenerated reports/charts are present in the working tree but have not yet been
committed or pushed. Check `git status` before doing any new work, and do not
discard these changes.

## 2. Research objective and staged discipline

The purpose of this project is to test whether two XAUUSD M15 setups have a
statistically credible edge after realistic trading costs. The workflow is
deliberately staged to reduce lookahead bias, data leakage, and overfitting.

The intended sequence is:

1. Stage 0: normalize and validate real market data.
2. Stage 1: generate causal signals, label trades, and manually audit charts.
3. Stage 2: evaluate train/validation and walk-forward out-of-sample results.
4. Stage 3: freeze the final rules and run the locked test exactly once.
5. Stage 4: create probability tables only for setups that pass.
6. Port passing logic to Pine Script and verify at least 95% signal parity.
7. Forward-test alerts for four to eight weeks.
8. Build a realtime backend only if a setup survives the earlier gates.

Stages must not be run as one uninterrupted batch. Reports and charts must be
reviewed between stages. A FAIL is a valid research result and must not be
converted into a PASS by changing thresholds after seeing the data.

## 3. Repository layout

| Path | Purpose |
|---|---|
| `config/research.json` | Frozen data assumptions, costs, setup parameters, split, and trial accounting |
| `run.py` | Main staged CLI and stage gates |
| `src/convert_m1_to_m15.py` | HistData M1 to normalized M15 UTC conversion |
| `src/reconstruct_2023.py` | Inserts only missing 2023 timestamps from the repair source |
| `src/data_quality.py` | CSV validation and data-quality reporting |
| `src/features.py` | ATR, EMA, ADX, H1/H4 feature construction |
| `src/gaps.py` | Weekend, daily break, holiday, and unexpected-gap classification |
| `src/signals_a.py` | Setup A signal rules |
| `src/signals_b.py` | Preserved B_v1 and active B_v2 signal rules |
| `src/pipeline.py` | Combined signals, causal gap filter, and no-lookahead test |
| `src/labeling.py` | Cost-aware triple-barrier labeling |
| `src/stage1.py` | Stage 1 CSV/JSON reports, audits, and chart generation |
| `src/split_lock.py` | Chronological split and locked-test hashing |
| `src/evaluate.py` | Early Stage 2 evaluation primitives; not yet exposed by an approved stage |
| `src/walkforward.py` | Placeholder for gated Stage 2 walk-forward work |
| `tests/` | Unit, regression, no-lookahead, and artifact tests |
| `reports/` | Generated research artifacts |
| `data/` | Local normalized data; ignored by Git |
| `rawdata/` | Local provider files; ignored by Git |
| `DECISIONS.md` | Formal record of the B_v2 research decision |

## 4. Data history and the active dataset

### 4.1 Primary source

The primary source is HistData MetaTrader XAUUSD M1 BID data covering roughly
2020 through 2026. Volume is not used.

The original source clock required special handling. Although the provider's
general documentation describes fixed EST, direct price alignment against an
independent Dukascopy-derived BID series showed that this downloaded dataset
uses:

- UTC-05 in winter.
- UTC-04 from the last Sunday of March until the day before the last Sunday of
  October.

This resembles an EST-style clock using European DST transition dates. It is
not fixed EST and is not standard `America/New_York` DST. The rule is explicit
in `src/convert_m1_to_m15.py` and covered by regression tests.

### 4.2 Conversion to M15 UTC

The converter reads:

`rawdata/merged/XAUUSD_M1_FULL.csv`

and writes:

`data/XAUUSD_M15_UTC.csv`

The conversion performs these operations:

- Converts the empirically verified source clock to UTC.
- Treats each timestamp as the bar-open time.
- Aggregates M1 bars into UTC-aligned M15 buckets.
- Drops 360 exact duplicate M1 rows.
- Conservatively merges 778 conflicting duplicate M1 rows using first open,
  maximum high, minimum low, and last close.
- Records `m1_count` for each resulting M15 bar.
- Ignores and omits volume.

The conversion report is `reports/histdata_conversion.json`.

### 4.3 Why 2023 was reconstructed

The original normalized file had severe missing-data problems in 2023,
especially from March through July. Filling prices by interpolation would have
created artificial market behavior, so that approach was rejected.

Instead, missing timestamps were sourced from EV Trading Labs annual XAUUSD M15
BID data derived from Dukascopy ticks. The reconstruction policy was:

- Never overwrite a HistData bar.
- Insert only a timestamp absent from HistData.
- Use only BID OHLC from the repair source.
- Preserve a `source` field on every row.
- Leave `m1_count` blank for inserted bars because the repair input was already
  aggregated to M15.

Compatibility checks used 20,600 overlapping 2023 bars. Median and p99 absolute
OHLC differences were approximately USD 0.005, and all compatibility gates
passed.

The reconstruction inserted 2,944 bars:

| Month | Inserted bars |
|---|---:|
| 2023-01 | 12 |
| 2023-02 | 180 |
| 2023-03 | 584 |
| 2023-04 | 484 |
| 2023-05 | 600 |
| 2023-06 | 562 |
| 2023-07 | 522 |

The active file is:

`data/XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv`

Its required SHA-256 is:

`99654161cc09ab9336e20063666b467250b0181151dab366ed1ead162e68d3f6`

Active CSV columns are:

`timestamp, open, high, low, close, m1_count, source`

Source values distinguish original HistData bars from repair bars, including
`HISTDATA_M1_BID` and `EVTL_DUKASCOPY_M15_BID_FILL`.

### 4.4 Active data-quality results

| Item | Result |
|---|---:|
| UTC range | 2020-01-01 23:00 to 2026-10-02 20:45 |
| Total rows | 159,471 |
| Span | About 6.75 years |
| Fatal errors | 0 |
| Bad OHLC ticks | 0 |
| Conflicting M15 timestamps | 0 |
| Remaining unexpected gap events | 53 |
| Estimated bars in unexpected gaps | 1,258 |
| M15 rows with fewer than 15 M1 bars | 459 |
| M15 rows with fewer than 14 M1 bars | 117 |
| Repair rows with blank `m1_count` | 2,944 |

Before reconstruction, the full dataset had 646 unexpected-gap events covering
an estimated 4,270 bars. After reconstruction it had 53 events covering 1,258
bars. In 2023 specifically, the count fell from 601 events / 3,123 bars to 8
events / 111 bars. Of those 111 remaining 2023 bars, 108 are associated with
seven US holiday early-close intervals. One remaining event is on 2023-09-25.

Missing bars are never silently synthesized by Stage 0 or Stage 1.

## 5. Frozen train, validation, and test split

The split is chronological by row count using 60% / 20% / 20%:

| Partition | UTC range | Rows |
|---|---|---:|
| Train | 2020-01-01 23:00 through 2024-01-22 10:00 | 95,682 |
| Validation | 2024-01-22 10:15 through 2025-05-28 09:15 | 31,894 |
| Test | 2025-05-28 09:30 through 2026-10-02 20:45 | 31,895 |

The locked test partition SHA-256 is:

`1a96e7873bd39b43f2570d6e8b810a0e2b83422878e19a953cbfb4e6c7f89fec`

Stage 1 loads only train plus validation. Before doing so, it recomputes the
split and verifies the test-partition hash, timestamps, and row counts against
the config. A changed dataset therefore blocks Stage 1 instead of silently
moving the split.

The locked test has not been evaluated or used to tune the strategy.

## 6. Lookahead prevention

Lookahead protection is a core acceptance condition, not an optional check.

The implementation uses:

- Previous fully closed H1 buckets for H1 EMA50, EMA200, and ADX.
- Previous fully closed H4 buckets where H4 mapping is tested.
- Pivot confirmation only after the configured five right-hand bars exist.
- Next-bar entry after a signal.
- A deterministic prefix-versus-full-history comparison at 50 timestamps.

For every sampled timestamp, the pipeline regenerates signals and higher-timeframe
features using only the prefix ending at that timestamp and compares them with
the corresponding full-history result. The current result is PASS at 50/50
timestamps with fixed seed `20261010`.

If this test ever fails, stop immediately. No downstream metric is valid until
the failure is explained and fixed.

## 7. Gap classification and policies

`src/gaps.py` classifies every discontinuity as one of:

- `WEEKEND_OR_WEEKEND_CROSSING`
- `SCHEDULED_DAILY_METALS_BREAK`
- `SCHEDULED_HOLIDAY_BREAK`
- `UNEXPECTED`

The shared Stage 1 policies are:

- A signal is suppressed if an unexpected gap occurred within the preceding
  100 M15 bars.
- If the scheduled entry bar is not exactly 15 minutes after the signal bar,
  the signal is skipped as `SKIPPED_ENTRY_GAP`.
- An open trade is never simulated through a discontinuity. It is closed on the
  last contiguous bar as `TIMEOUT` and marked `truncated_by_gap`.
- Stage 2 must report metrics both including and excluding truncated trades.

B_v2 adds a pivot-specific rule: the 11-bar pivot window and the interval from
pivot to sweep must not contain a weekend, scheduled holiday, or unexpected
gap. Scheduled daily metals breaks were counted but remain allowed.

## 8. Setup A implementation

Setup A is a trend-pullback continuation setup. Its numeric parameters have not
been changed.

Frozen parameters:

| Parameter | Value |
|---|---:|
| ATR length | 14 |
| M15 EMA | 20 |
| H1 fast EMA | 50 |
| H1 slow EMA | 200 |
| H1 ADX length / threshold | 14 / 20 |
| Pullback lookback | 20 bars |
| Pullback depth | 0.8 to 2.5 ATR |
| EMA touch allowance | 0.3 ATR |
| Stop buffer | 0.25 ATR |

Long logic:

1. Previous closed H1 EMA50 is above EMA200 and ADX14 is above 20.
2. Pullback depth from the highest high of the last 20 M15 bars to the current
   low is between 0.8 and 2.5 ATR.
3. The bar reaches the EMA20 allowance and closes above EMA20.
4. The bar is bullish and closes in the upper 40% of its range.
5. Stop loss is below the minimum low of the signal bar and previous three bars,
   plus a 0.25 ATR buffer.

Short logic is the symmetric version: bearish H1 trend, pullback from the
20-bar low, EMA20 rejection, bearish candle closing in the lower 40%, and stop
above the recent four-bar high plus 0.25 ATR.

The signal is recorded on the confirmation bar and entry is scheduled for the
next M15 bar.

Current kept Setup A signals are 4,745 long and 3,104 short, or 7,849 total.
Normal-cost labeled trades are 2,136 train and 694 validation, or 2,830 total.

## 9. Setup B history and B_v2 implementation

### 9.1 Original B_v1

B_v1 used strict pivot lows/highs with five bars on each side, a maximum pivot
age of 60 bars, a 0.1 ATR sweep buffer, up to three confirmation bars, and a
0.25 ATR stop buffer.

The phrase "not previously swept" was audited. B_v1 only marked a pivot as
swept after a fully qualifying wick-through-and-close-back event. It did not
invalidate the pivot when price had already traded or closed beyond it without
meeting the complete sweep rule.

The audit found that 2,759 of 4,268 kept B_v1 signals, or 64.64%, used a pivot
after price had already closed beyond it. This was considered inconsistent with
the intended market-structure meaning.

The B_v1 gap audit also found:

| Interval | Any gap | Weekend | Holiday | Daily break | Unexpected |
|---|---:|---:|---:|---:|---:|
| 11-bar pivot window | 546 | 111 | 13 | 422 | 0 |
| Pivot through sweep | 1,047 | 190 | 38 | 819 | 0 |

There were 193 weekend gap events across the 190 affected pivot-to-sweep
signals. No expectancy or profit factor was computed for B_v1.

### 9.2 B_v2 decision

B_v1 remains in `src/signals_b.py`. Selection is controlled by:

```json
"setup_b": {
  "version": "v2"
}
```

B_v2 keeps every original numeric B parameter unchanged and changes only pivot
validity and discontinuity handling:

1. A strict pivot has five bars on each side and ties are not pivots.
2. It becomes available only after the five right-hand bars have closed.
3. A pivot low is invalidated by the first later bar with `low < pivot_price`.
4. A pivot high is invalidated by the first later bar with `high > pivot_price`.
5. Only that first-pierce bar may qualify as the sweep.
6. A long sweep must extend at least 0.1 ATR below the pivot and close back
   above it. A short sweep must extend at least 0.1 ATR above the pivot and
   close back below it.
7. After a valid long sweep, confirmation requires a close above the sweep-bar
   high within three bars. A short requires a close below the sweep-bar low.
8. Entry is scheduled for the next M15 bar.
9. Stop loss is 0.25 ATR beyond the sweep extreme.
10. Weekend, holiday, or unexpected gaps invalidate the pivot window or the
    pivot-to-sweep interval. Daily metals breaks remain allowed.

The B_v2 pivot audit result is:

- 1,222 kept signals checked.
- 0 strict 5/5 violations.
- 0 prior pierces beyond the pivot before the selected sweep.
- 0 prior closes beyond the pivot before the selected sweep.

The B_v2 change counts as another hypothesis trial. Trial accounting is now:

- Setup A: `n_trials = 1`
- Setup B: `n_trials = 2`
- Global: `n_trials = 3`

This is recorded in both `config/research.json` and `DECISIONS.md`.

## 10. Labeling and execution model

Labels are generated causally from BID M15 bars.

Frozen labeling parameters:

| Parameter | Value |
|---|---:|
| Reward/risk target | 2.0 R |
| Maximum holding time | 32 M15 bars |
| Minimum accepted risk | 0.8 ATR |
| Maximum accepted risk | 3.0 ATR |
| Same-bar SL and TP | Count as LOSS |

Position overlap is prevented independently for each `(setup, direction)`
pair. A new signal for the same setup and direction is skipped until the prior
trade has exited.

Because input data is BID-only, side-aware execution is essential:

- Long entry: next BID open + spread + adverse slippage.
- Long stop, target, and exit barriers: evaluated on BID OHLC.
- Short entry: next BID open - adverse slippage.
- Short stop, target, and exit barriers: evaluated on synthetic ASK, calculated
  as BID + spread.

Base costs are USD 0.30 spread and USD 0.10 adverse slippage per trade. Labels
are independently regenerated for three scenarios:

- Normal: 1.0x costs.
- Costs +50%: 1.5x costs.
- Costs +100%: 2.0x costs.

The accepted trade set may differ between scenarios because changed entry cost
changes risk, TP, risk filters, barrier hits, and position-overlap timing. It is
therefore normal for the +100% trade count not to be a monotonic continuation
of the +50% count.

## 11. Current Stage 1 results

Only train and validation data are included.

### 11.1 Kept signals

| Setup | Long | Short | Total |
|---|---:|---:|---:|
| A | 4,745 | 3,104 | 7,849 |
| B_v2 | 624 | 598 | 1,222 |
| Total | 5,369 | 3,702 | 9,071 |

The 100-bar unexpected-gap filter suppressed 19 raw signals: 14 A-short,
2 B_v2-long, and 3 B_v2-short.

### 11.2 Normal-cost labeled trades by split

| Setup | Train | Validation | Total |
|---|---:|---:|---:|
| A | 2,136 | 694 | 2,830 |
| B_v2 | 760 | 248 | 1,008 |
| Total | 2,896 | 942 | 3,838 |

B_v2 signal and trade detail:

| Split | Long signals | Short signals | Total signals | Long trades | Short trades | Total trades |
|---|---:|---:|---:|---:|---:|---:|
| Train | 475 | 444 | 919 | 383 | 377 | 760 |
| Validation | 149 | 154 | 303 | 118 | 130 | 248 |

The frozen Stage 2 rule requires at least 300 labeled validation trades per
setup. B_v2 currently has 248 under normal costs. This count is known before
Stage 2, but no edge metric has been computed. Do not lower or reinterpret the
300-trade threshold after seeing this result.

### 11.3 Labeling audit

| Item | Count |
|---|---:|
| Input signals | 9,071 |
| Labeled trades | 3,838 |
| Skipped signals | 5,233 |
| Position already open | 4,560 |
| Risk above 3 ATR | 483 |
| Risk below 0.8 ATR | 103 |
| Entry crosses a gap | 86 |
| Incomplete development horizon | 1 |
| Trades truncated before a gap | 658 |
| Same-bar SL and TP counted as loss | 6 |

Truncated trades comprise 506 daily-break cases, 136 weekend cases, 16 holiday
cases, and 0 unexpected-gap cases.

### 11.4 Cost scenario counts

| Scenario | Labeled trades | Skipped | Truncated |
|---|---:|---:|---:|
| Normal | 3,838 | 5,233 | 658 |
| Costs +50% | 3,822 | 5,249 | 654 |
| Costs +100% | 3,823 | 5,248 | 651 |

B_v2 labeled trades by split and cost scenario:

| Scenario | Train | Validation |
|---|---:|---:|
| Normal | 760 | 248 |
| Costs +50% | 743 | 247 |
| Costs +100% | 724 | 245 |

These are counts and implementation diagnostics only. No expectancy, profit
factor, confidence interval, or GO/NO-GO result has been computed for B_v1 or
B_v2.

## 12. Chart audit implementation

Stage 1 generates exactly 20 deterministic chart examples per setup.

Setup A charts contain:

- At least 60 bars before the signal.
- EMA20.
- Rolling 20-bar highest high and lowest low.
- Previous-closed H1 EMA50, EMA200, and ADX annotation.
- Signal, entry, stop, target, and exit markers.

Setup B_v2 charts contain:

- At least 80 bars before the signal.
- A horizontal pivot line labeled with its price.
- The pivot bar marker.
- An orange highlight on the sweep bar.
- Signal, entry, stop, target, and exit markers.

Purple shading identifies bars inserted from the Dukascopy-derived repair
source. Chart candles are BID, and the chart note explains the BID/ASK execution
convention.

The current manual-review directories are:

- `reports/charts/setup_a`
- `reports/charts/setup_b`
- `reports/charts/setup_a_contact_sheet.png`
- `reports/charts/setup_b_contact_sheet.png`

The contact sheets are useful for overview only. Full-resolution individual
PNGs must be used to verify the pivot, sweep, candle close, and stop/target
geometry.

## 13. How to set up and reproduce the project

Run commands from `E:\SelfProject` in PowerShell.

### 13.1 Create the environment

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

The reproducible environment used so far is Python 3.8.10 with Matplotlib
3.7.5. The project intentionally has a small dependency set.

### 13.2 Run tests

```powershell
.\.venv\Scripts\python -B -m unittest discover -s tests -v
```

If the virtual environment has not been created, the system interpreter can be
used if it has Matplotlib 3.7.5:

```powershell
python -B -m unittest discover -s tests -v
```

Current expected result: 31 tests PASS.

### 13.3 Rebuild data only when necessary

The conversion and reconstruction scripts use fixed path constants rather than
CLI arguments:

```powershell
python src\convert_m1_to_m15.py
python src\reconstruct_2023.py
```

Important: these scripts do not implement an argparse `--help` option. Passing
`--help` still executes their normal `main()` function. Verify the fixed input
paths before running them.

After rebuilding, confirm that the active file hash still matches the frozen
hash. If it changes, do not silently update the config or split lock.

### 13.4 Run Stage 0

```powershell
python run.py --stage data --data .\data\XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv
```

Use `--assume-utc` only for a known-UTC source that lacks timezone offsets. The
active normalized file already contains offsets and does not need this flag.

Required Stage 0 outputs include:

- `reports/data_quality.json`
- `reports/corrections.jsonl`
- `reports/stage0_run.json`
- `reports/STAGE0_SUMMARY.md`
- `reports/2023_reconstruction.json`
- `reports/timezone_verification_2020_2026.json`

### 13.5 Run Stage 1 signals

```powershell
python run.py --stage signals
```

This command uses the normalized path in `config/research.json`, checks the
locked split, generates Setup A plus active B_v2 signals, applies the unexpected
gap filter, and reruns the 50-timestamp no-lookahead test.

### 13.6 Run Stage 1 labeling and charts

```powershell
python run.py --stage label
```

This command requires a successful `reports/stage1_signals.json`. It:

- Regenerates active signals.
- Labels normal, +50%, and +100% cost scenarios.
- Regenerates the legacy B_v1 signal set only for the frozen gap audit.
- Regenerates the active B_v2 pivot audit and split counts.
- Deletes obsolete example PNGs and produces exactly 20 per setup.
- Writes the signal, trade, skipped-signal, JSON, Markdown, and contact-sheet
  artifacts.

### 13.7 Do not run later CLI stages yet

Although `run.py` accepts the names `evaluate`, `wf`, `final_test`, and `table`,
they currently return `BLOCKED`. Stage 2 has not been approved or fully wired
into the CLI. Do not bypass this gate.

## 14. Important reports and how to read them

Read these files in this order:

1. `reports/STAGE0_SUMMARY.md`
2. `reports/data_quality.json`
3. `reports/2023_reconstruction.json`
4. `reports/STAGE1_SUMMARY.md`
5. `DECISIONS.md`
6. `reports/STAGE1_SETUP_B_V1_GAP_AUDIT.md`
7. `reports/STAGE1_SETUP_B_V2_PIVOT_AUDIT.md`
8. `reports/STAGE1_SETUP_B_V2_COUNTS.md`
9. Individual chart PNGs in `reports/charts/setup_a` and `setup_b`
10. `reports/stage1_signals.json` and `reports/stage1_label.json`

For a later Stage 2 report, inspect in this priority order:

1. PASS or FAIL under frozen gates.
2. Number of trades.
3. Net expectancy in R and the lower bound of its 95% confidence interval.
4. Results after +50% costs, and additionally the implemented +100% scenario.
5. Results including and excluding truncated trades.
6. Stability by year, quarter, direction, and walk-forward window.
7. `n_trials` and the risk of selection bias.

Suspicious results include a win rate above 65% at 1:2 reward/risk, profit
factor above 2, or an almost straight equity curve. These patterns require a
fresh lookahead and cost-model audit rather than celebration.

## 15. Current automated test coverage

The 31-test suite covers:

- Naive timestamp rejection and explicit UTC correction behavior.
- Exact and conflicting duplicate handling.
- Gap reporting without filling.
- Source-column preservation.
- HistData empirical timezone conversion and DST boundary behavior.
- Holiday and daily metals-break classification.
- BID/ASK cost-model behavior for long and short trades.
- Gap truncation and entry-gap skipping.
- Same-bar SL/TP loss policy.
- Cost-scenario scaling.
- H1 previous-bucket behavior.
- Pivot right-bar confirmation delay.
- Full-history versus prefix no-lookahead comparison.
- B_v2 first-pierce invalidation.
- Valid B_v2 first-pierce sweep behavior.
- B_v2 weekend rejection and daily-break allowance.
- Frozen split/hash and Stage 1 artifact contracts.
- Exact chart counts and chart overlay specifications.
- B_v1 gap-audit counts and B_v2 zero-prior-pierce audit.
- Stable Setup A and B_v2 signal counts.

Whenever signal, feature, gap, split, labeling, or report code changes, rerun the
entire suite. A focused test is not a substitute for the 50-timestamp causal
test.

## 16. Non-negotiable rules for future work

- Do not change Setup A numeric parameters.
- Do not change Setup B numeric parameters while reviewing B_v2.
- Keep B_v1 available behind the version flag.
- Do not calculate or report B_v1 expectancy or profit factor.
- Do not inspect the locked test partition before final rules are frozen.
- Do not lower the 300-validation-trade rule after seeing B_v2's count of 248.
- Do not synthesize missing market bars.
- Do not mix BID, ASK, and midpoint data without an explicit new research
  decision and separate dataset identity.
- Do not remove the side-aware spread and slippage model.
- Do not let trades jump across gaps.
- Do not remove `source` or `strategy_version` lineage fields.
- Do not reset `n_trials`; B_v2 is an additional trial.
- Do not build Pine alerts, a realtime backend, AI features, or news ingestion
  before a setup passes the research gates.

## 17. Security and repository hygiene

Market data, downloaded archives, provider caches, virtual environments, and
temporary files are intentionally ignored by Git. In particular, never commit
cookies, session files, or `.tmp_*` files. The `.gitignore` already excludes:

- `data/`, `rawdata/`, and `download/`
- `.dukascopy-cache/` and `.tmp_dukascopy_pkg/`
- `.tmp_*`, ZIP, JAR, and wheel files
- virtual environments and Python bytecode

Before pushing, inspect both normal and ignored files and confirm no provider
credential or cookie has entered Git history. Keep the repository private if
there is any uncertainty about historical secrets or source-data redistribution
rights.

## 18. Next actions

The immediate next step is not Stage 2. It is manual Stage 1 approval:

1. Review all 20 full-resolution Setup A charts and confirm the pullback,
   higher-timeframe trend, entry, stop, target, and exit are visually correct.
2. Review all 20 full-resolution B_v2 charts and confirm strict pivots,
   first-pierce sweeps, close-back behavior, confirmation, entry, stop, and gap
   context.
3. Review `STAGE1_SETUP_B_V2_PIVOT_AUDIT.md` and confirm the zero-prior-pierce
   result.
4. Review `STAGE1_SETUP_B_V2_COUNTS.md`, including the fact that validation has
   only 248 normal-cost B_v2 trades.
5. Obtain explicit human approval or rejection of Stage 1.
6. Review and commit the current B_v2 working-tree changes before starting a
   new implementation stage.

Only after explicit approval should Stage 2 be implemented and run. Stage 2
must use the frozen gates, count all hypothesis trials, report cost scenarios,
show both truncation views, and perform walk-forward evaluation. If a setup
fails its sample-size or statistical gate, record FAIL and do not proceed with
that setup to the locked final test.

## 19. Quick context for a future assistant

When resuming this project, begin with the following checklist:

1. Read this file, `README.md`, `DECISIONS.md`, and
   `reports/STAGE1_SUMMARY.md`.
2. Inspect `git status`; the B_v2 work may still be uncommitted.
3. Confirm the active dataset hash and `setup_b.version = "v2"`.
4. Run `python -B -m unittest discover -s tests -v` and expect 31 PASS.
5. Do not run Stage 2 unless the user explicitly says Stage 1 is approved.
6. Preserve the locked test and every frozen parameter/gate.
7. Treat B_v2 as trial 2 for Setup B and trial 3 globally.
8. Lead with evidence from generated reports rather than assumptions.

The most important current fact is: the data and causal Stage 1 machinery are
working, B_v1's pivot semantics were audited and replaced by a versioned B_v2,
and B_v2 now passes the structural pivot audit. This does not demonstrate a
trading edge. Manual chart approval and the pre-registered Stage 2 gates still
stand between the current code and any claim of robustness.
