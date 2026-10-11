# Timezone verification against Dukascopy-derived BID M15

Status: **PASS**

The test compared every available ISO week at timestamp shifts -60, 0,
and +60 minutes. A week disagrees only when a nonzero shift improves the
median close-price match by more than 0.02 USD.

| Year | Direct overlap | Median close diff (USD) | p99 close diff (USD) | Weeks checked | Disagreements |
|---:|---:|---:|---:|---:|---:|
| 2020 | 23,626 | 0.002000 | 0.005000 | 53 | 0 |
| 2021 | 23,562 | 0.002000 | 0.005000 | 52 | 0 |
| 2022 | 23,643 | 0.002000 | 0.005000 | 52 | 0 |
| 2023 | 20,600 | 0.005000 | 0.005000 | 52 | 0 |
| 2024 | 23,713 | 0.005000 | 0.005000 | 53 | 0 |
| 2025 | 23,606 | 0.005000 | 0.005000 | 53 | 0 |
| 2026 | 17,776 | 0.000000 | 0.000000 | 40 | 0 |

No disagreement week means the zero-minute alignment remained the
best price match, including the available late-March and late-October/
early-November focus weeks.

The 2026 HistData file ends on 2026-10-02, so its late-October and
early-November weeks do not exist and cannot be tested.
