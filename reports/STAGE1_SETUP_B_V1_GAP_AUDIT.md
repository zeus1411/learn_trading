# Stage 1 Setup B_v1 gap audit

Scope: 4,268 kept B_v1 signals after the unexpected-gap filter.

| Interval | Any gap | Weekend | Holiday | Daily break | Unexpected |
|---|---:|---:|---:|---:|---:|
| 11-bar pivot window | 546 | 111 | 13 | 422 | 0 |
| Pivot bar through sweep bar | 1047 | 190 | 38 | 819 | 0 |

Counts by type are numbers of signals containing at least one gap of that type.
The pivot-to-sweep interval contains 193 weekend gap events across 190 signals;
all other listed signal counts equal their event counts.

B_v2 rejects weekend, holiday, and unexpected gaps. Daily metals breaks are
counted but remain allowed, following the recorded decision.
