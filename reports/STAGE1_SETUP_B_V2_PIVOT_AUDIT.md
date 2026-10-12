# Stage 1 Setup B_v2 pivot audit

Status: **PASS**

Scope: Stage 1 kept Setup B signals after the unexpected-gap filter.

- Signals checked: 1222
- Strict 5-left/5-right pivot violations: 0
- Prior close beyond pivot before sweep: 0 (0.00%)
- Long: 0
- Short: 0
- Prior pierce beyond pivot before sweep: 0 (0.00%)

B_v2 invalidates a pivot on the first bar that trades beyond it. Only that
first-pierce bar may qualify as the sweep; it must meet the ATR buffer and
close-back-inside rules. The zero count above is the required audit result.
