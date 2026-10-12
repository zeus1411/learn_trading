# Stage 0 checkpoint - XAUUSD M15 reconstructed 2023

Status: **PASS**

The active UTC M15 file passes structural validation and the deterministic
50-timestamp no-lookahead test. Stage 1 has not been started.

## Active dataset

- Instrument / side: XAUUSD BID
- Primary source: HistData MetaTrader M1 bars
- Repair source: EV Trading Labs annual M15 BID data, derived from Dukascopy
  ticks
- Timestamp semantics: UTC bar open
- Active file: `data/XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv`
- Range: 2020-01-01 23:00 UTC to 2026-10-02 20:45 UTC
- Rows: 159,471
- SHA-256: `99654161cc09ab9336e20063666b467250b0181151dab366ed1ead162e68d3f6`

The original merged M1 file, source ZIP files, and base normalized M15 file were
not modified.

## Timezone correction

The provider's generic documentation describes fixed EST, but that rule does
not align this downloaded XAUUSD dataset with the independent repair source.
Price matching across 2022-2024 shows that the actual source clock uses UTC-05
in winter and UTC-04 from the last Sunday of March through the day before the
last Sunday of October. This empirical rule is explicit in the converter and
covered by regression tests.

## 2023 reconstruction

- Existing HistData bars preserved: all
- Overlapping 2023 bars used for compatibility checks: 20,600
- HistData timestamps absent from repair source: 0
- Bars inserted: 2,944
- Inserted by month: Jan 12, Feb 180, Mar 584, Apr 484, May 600, Jun 562,
  Jul 522
- Median and p99 absolute OHLC difference on overlap: approximately 0.005 USD
- Compatibility gates: all PASS

Inserted rows retain a `source` label and blank `m1_count`. No price was
interpolated and no existing HistData OHLC value was replaced.

## Before and after

| Metric | Before repair | After repair |
|---|---:|---:|
| Total rows | 156,527 | 159,471 |
| Unexpected gaps, full dataset | 646 events / 4,270 bars | 53 events / 1,258 bars |
| Unexpected gaps, 2023 | 601 events / 3,123 bars | 8 events / 111 bars |

Of the 111 remaining 2023 bars classified as unexpected, 108 fall inside seven
US holiday early-close intervals. The remaining event is a 60-minute interval
on 2023-09-25 (three absent M15 bars). These were not synthesized.

## Source-data observations

- 360 exact duplicate M1 rows were dropped during base conversion.
- 778 conflicting duplicate M1 rows were merged conservatively: first open,
  maximum high, minimum low, last close.
- 459 original M15 bars contain fewer than 15 source M1 bars; 117 contain fewer
  than 14.
- `m1_count` is unavailable for the 2,944 already-aggregated repair bars, so its
  validation status is `PARTIAL`, not an OHLC failure.
- No malformed prices, OHLC-order violations, or conflicting M15 timestamps
  remain.

## Lookahead verification

- Fixed seed: 20261010
- Prefix-versus-full timestamps: 50
- Full signal count used by the causal test: 15,111
- Result: PASS

## Gate

Stage 0 is complete. Do not start Stage 1 until this reconstructed dataset and
the residual-gap policy are explicitly approved.
